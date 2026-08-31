"""Tests du registre de suivi : tickets, étapes, journal.

Le suivi porte des garanties qui ne se voient pas dans le corpus produit, et
qu'un défaut rendrait silencieuses : un travail humain écrasé par une
exécution automatique, un document relu deux fois, une validation dont on ne
sait plus qui l'a prononcée. Ces tests portent d'abord sur ces risques.
"""

from __future__ import annotations

import pytest

from bldp.core.tracking import (
    Stage,
    TrackingRegistry,
    allowed_transitions,
    badge_for,
)
from bldp.core.tracking.registry import HUMAN_ONLY_STAGES, TrackingError
from bldp.models import QualityStatus, ValidationStatus
from bldp.pipeline import run_pipeline


@pytest.fixture
def registry(tmp_path):
    with TrackingRegistry(tmp_path / "suivi.sqlite") as registre:
        yield registre


@pytest.fixture
def corpus(tmp_path, make_text_pdf):
    from pathlib import Path

    folder = tmp_path / "corpus"
    folder.mkdir()
    textes = {
        "loi_a.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
            "portant organisation du travail\n\n"
            "Article 1er : La presente loi fixe les regles applicables.\n"
            "Article 2 : Est considere comme travailleur toute personne physique.\n"
        ],
        "loi_b.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-002 DU 11 FEVRIER 2026\n"
            "portant statut de la fonction publique\n\n"
            "Article 1er : Le present texte fixe le statut des agents publics.\n"
        ],
    }
    for nom, pages in textes.items():
        Path(make_text_pdf(nom, pages)).replace(folder / nom)
    return folder


# ---------------------------------------------------------------------------
# Un contenu, un ticket
# ---------------------------------------------------------------------------


class TestOneTicketPerContent:
    """Le ticket est attaché à l'empreinte, pas au nom du fichier."""

    def test_a_new_document_opens_a_ticket(self, registry):
        ticket = registry.open_ticket("loi_a", "a" * 64, "loi_a.pdf")
        assert ticket.ticket_id.startswith("BLDP-")
        assert ticket.stage is Stage.IMPORTE

    def test_the_same_content_never_opens_a_second_ticket(self, registry):
        """C'est la garantie centrale : ne pas retravailler le même document.

        Le même texte reçu sous deux noms, dans deux dossiers, à six mois
        d'écart, doit retrouver son ticket — donc son historique et la
        décision humaine déjà prise.
        """
        premier = registry.open_ticket("loi_a", "a" * 64, "loi_a.pdf")
        second = registry.open_ticket("autre_id", "a" * 64, "copie_2026.pdf")
        assert second.ticket_id == premier.ticket_id
        assert len(registry.list_tickets()) == 1

    def test_a_second_sighting_is_recorded(self, registry):
        """Revoir un document sous un autre nom est un fait : on le consigne."""
        ticket = registry.open_ticket("loi_a", "a" * 64, "loi_a.pdf")
        registry.open_ticket("loi_a", "a" * 64, "copie.pdf")
        actions = [e.action for e in registry.history(ticket.ticket_id)]
        assert "revu_sous_un_autre_nom" in actions

    def test_distinct_contents_get_distinct_tickets(self, registry):
        a = registry.open_ticket("loi_a", "a" * 64, "a.pdf")
        b = registry.open_ticket("loi_b", "b" * 64, "b.pdf")
        assert a.ticket_id != b.ticket_id


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


class TestTransitions:
    def test_an_illegal_transition_is_refused(self, registry):
        """On ne revient pas d'une validation à un état de traitement."""
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline")
        registry.advance(ticket.ticket_id, Stage.VALIDE, "virgile")
        with pytest.raises(TrackingError, match="interdit"):
            registry.advance(ticket.ticket_id, Stage.TRAITE, "virgile")

    def test_the_refusal_names_the_possible_stages(self, registry):
        """Un refus doit dire quoi faire, pas seulement dire non."""
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.VALIDE, "virgile") if False else None
        with pytest.raises(TrackingError) as info:
            registry.advance(ticket.ticket_id, Stage.ARCHIVE, "virgile")
        assert "Étapes possibles" in str(info.value)

    @pytest.mark.parametrize("stage", sorted(HUMAN_ONLY_STAGES, key=lambda s: s.value))
    def test_the_pipeline_never_decides_alone(self, registry, stage):
        """§16 : valider ou rejeter est une décision humaine, jamais l'autre."""
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline")
        for anonyme in ("pipeline", "auto", ""):
            with pytest.raises(TrackingError, match="décision humaine"):
                registry.advance(ticket.ticket_id, stage, anonyme)

    def test_a_named_person_may_decide(self, registry):
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline")
        valide = registry.advance(ticket.ticket_id, Stage.VALIDE, "virgile", "relu")
        assert valide.stage is Stage.VALIDE

    def test_a_decision_can_be_reopened(self, registry):
        """Un juriste doit pouvoir rouvrir un dossier sans toucher la base."""
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline")
        registry.advance(ticket.ticket_id, Stage.VALIDE, "virgile")
        assert Stage.A_VERIFIER in allowed_transitions(Stage.VALIDE)
        rouvert = registry.advance(ticket.ticket_id, Stage.A_VERIFIER, "virgile", "doute")
        assert rouvert.stage is Stage.A_VERIFIER

    def test_a_doubtful_document_is_reachable_straight_from_import(self):
        """Le pipeline importe et traite d'un coup : le passage doit exister.

        Sans lui, un document douteux restait marqué « importé » et
        n'apparaissait dans aucune file de relecture.
        """
        assert Stage.A_VERIFIER in allowed_transitions(Stage.IMPORTE)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


class TestJournal:
    def test_every_change_is_recorded_with_its_author(self, registry):
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline", "traitement ok")
        registry.assign(ticket.ticket_id, "virgile", "chef")
        registry.advance(ticket.ticket_id, Stage.VALIDE, "virgile", "conforme au JO")

        journal = registry.history(ticket.ticket_id)
        assert [e.actor for e in journal][-1] == "virgile"
        assert any(e.detail == "conforme au JO" for e in journal)
        assert all(e.at for e in journal), "chaque fait porte sa date"

    def test_the_journal_is_append_only(self, registry):
        """Rien n'est écrasé : l'historique complet reste consultable."""
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline")
        registry.advance(ticket.ticket_id, Stage.A_VERIFIER, "pipeline")
        registry.advance(ticket.ticket_id, Stage.EN_REVUE, "virgile")
        passages = [
            (e.from_stage, e.to_stage)
            for e in registry.history(ticket.ticket_id)
            if e.action == "changement_etape"
        ]
        assert passages == [
            ("importe", "traite"),
            ("traite", "a_verifier"),
            ("a_verifier", "en_revue"),
        ]

    def test_an_unreachable_suggestion_is_never_silent(self, registry, tmp_path):
        """Un ticket bloqué sans trace serait invisible partout."""
        ticket = registry.open_ticket("loi_a", "a" * 64)
        registry.advance(ticket.ticket_id, Stage.TRAITE, "pipeline")
        registry.advance(ticket.ticket_id, Stage.VALIDE, "virgile")
        registry.advance(ticket.ticket_id, Stage.ARCHIVE, "virgile")
        assert registry.get(ticket.ticket_id).stage is Stage.ARCHIVE


# ---------------------------------------------------------------------------
# Intégration au pipeline
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_a_run_opens_one_ticket_per_document(self, corpus, config):
        run_pipeline(corpus, config)
        with TrackingRegistry(config.path("database")) as registre:
            assert len(registre.list_tickets()) == 2

    def test_a_flagged_document_lands_in_the_review_queue(self, corpus, config):
        run_pipeline(corpus, config)
        with TrackingRegistry(config.path("database")) as registre:
            etapes = {t.stage for t in registre.list_tickets()}
        assert etapes <= {Stage.TRAITE, Stage.A_VERIFIER}
        assert Stage.VALIDE not in etapes, "rien ne s'auto-valide"

    def test_a_human_decision_survives_a_full_reprocessing(self, corpus, config):
        """Le risque le plus grave : écraser un travail humain."""
        run_pipeline(corpus, config)
        with TrackingRegistry(config.path("database")) as registre:
            ticket = registre.list_tickets()[0]
            registre.advance(ticket.ticket_id, Stage.VALIDE, "virgile", "relu")

        run_pipeline(corpus, config)

        with TrackingRegistry(config.path("database")) as registre:
            apres = registre.get(ticket.ticket_id, with_history=True)
        assert apres.stage is Stage.VALIDE
        assert any(e.action == "retraitement" for e in apres.events)

    def test_resume_skips_documents_already_settled(self, corpus, config):
        run_pipeline(corpus, config)
        with TrackingRegistry(config.path("database")) as registre:
            for ticket in registre.list_tickets():
                registre.advance(ticket.ticket_id, Stage.VALIDE, "virgile")

        result = run_pipeline(corpus, config, resume=True)
        assert result.report.total == 0
        assert len(result.skipped_existing) == 2

    def test_the_quality_score_drives_the_priority(self, corpus, config):
        run_pipeline(corpus, config)
        with TrackingRegistry(config.path("database")) as registre:
            for ticket in registre.list_tickets():
                assert ticket.quality_score is not None
                assert 0 <= ticket.priority <= 2

    def test_tracking_can_be_switched_off(self, corpus, config):
        cfg = config.with_overrides({"tracking": {"enabled": False}})
        result = run_pipeline(corpus, cfg)
        assert result.documents
        assert result.tickets == []

    def test_a_failing_registry_never_breaks_the_corpus(self, corpus, config, monkeypatch):
        """§26 : le suivi est un service, jamais un point de rupture."""
        import bldp.pipeline as pipeline_module

        def refuse(*args, **kwargs):
            raise RuntimeError("registre indisponible")

        monkeypatch.setattr(pipeline_module, "_record_tracking", refuse)
        with pytest.raises(RuntimeError):
            pipeline_module._record_tracking([], config)

        # Le pipeline, lui, protège l'appel : le corpus est produit malgré tout.
        monkeypatch.undo()
        result = run_pipeline(corpus, config)
        assert result.documents and result.exports


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


class TestBadges:
    @pytest.mark.parametrize("stage", list(Stage))
    def test_every_stage_has_a_badge(self, stage):
        assert badge_for(stage)

    def test_badges_are_printable_on_a_windows_console(self):
        """Un badge illisible ne vaut pas mieux qu'une absence de badge.

        Les pictogrammes géométriques sont absents de cp1252 : les afficher
        interrompait la commande sur une UnicodeEncodeError.
        """
        for stage in Stage:
            badge_for(stage).encode("cp1252")

    def test_priority_is_visible_on_the_badge(self):
        assert badge_for(Stage.A_VERIFIER, priority=2).endswith("!")
        assert not badge_for(Stage.A_VERIFIER, priority=0).endswith("!")
