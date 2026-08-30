"""Tests du module 8 — statuts, versions et relations juridiques (§13).

Le point sensible testé ici : le pipeline ne doit jamais **affirmer** un statut
juridique sur la foi d'un indice faible. Une relation incertaine est conservée
et signalée, pas appliquée.
"""

from __future__ import annotations

import pytest

from bldp.core.relations import (
    annotate_relations,
    assign_versions,
    build_reference_index,
    detect_relations,
    document_reference_key,
    group_versions,
    normalize_reference,
    relation_graph,
    resolve_relations,
    unresolved_relations,
)
from bldp.models import (
    Article,
    Document,
    DocumentMetadata,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    LegalStatus,
    Page,
    RelationType,
    SourceFile,
)
from bldp.utils import utc_now_iso


def build(
    document_id: str,
    text: str = "",
    doc_type: DocumentType = DocumentType.LOI,
    number: str | None = None,
    date: str | None = None,
    status: LegalStatus = LegalStatus.INCONNU,
) -> Document:
    source = SourceFile(
        document_id=document_id,
        source_path=f"/input/{document_id}.pdf",
        filename=f"{document_id}.pdf",
        extension=".pdf",
        size_bytes=1000,
        file_hash=(document_id * 8)[:64].ljust(64, "0"),
        ingested_at=utc_now_iso(),
    )
    pages = [
        Page(document_id=document_id, page=1, text=text, source_file=source.filename)
    ] if text else []
    return Document(
        document_id=document_id,
        source=source,
        metadata=DocumentMetadata(
            document_id=document_id,
            type=doc_type,
            number=number,
            date=date,
            status=status,
        ),
        extraction=ExtractionResult(
            document_id=document_id,
            source_file=source.filename,
            method=ExtractionMethod.NATIVE,
            pages=pages,
        ),
        articles=[
            Article(
                article_id=f"{document_id}_article_1",
                document_id=document_id,
                article_number="1",
                text=text[:200],
                page_start=1,
                page_end=1,
            )
        ] if text else [],
    )


# ---------------------------------------------------------------------------
# Normalisation des références
# ---------------------------------------------------------------------------


class TestReferenceNormalisation:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("la loi n° 2015-018", ("loi", "2015-018")),
            ("Loi N° 2015 - 18", ("loi", "2015-018")),
            ("le decret n° 2020-113", ("decret", "2020-113")),
            ("l'ordonnance n° 2021-02", ("ordonnance", "2021-002")),
        ],
    )
    def test_normalisation(self, raw, expected):
        assert normalize_reference(raw) == expected

    def test_padding_makes_variants_equal(self):
        """« 2015-18 » et « 2015-018 » désignent le même texte."""
        assert normalize_reference("loi n° 2015-18") == normalize_reference("loi n° 2015-018")

    @pytest.mark.parametrize(
        "raw",
        ["toutes dispositions anterieures contraires", "la presente loi", "", "n° 12"],
    )
    def test_vague_reference_is_not_normalised(self, raw):
        """Une formule vague ne doit jamais être rapprochée d'un texte précis."""
        assert normalize_reference(raw) is None

    def test_document_key(self):
        document = build("loi_a", number="2026-1", doc_type=DocumentType.LOI)
        assert document_reference_key(document) == ("loi", "2026-001")

    def test_document_without_number_has_no_key(self):
        assert document_reference_key(build("sans_numero")) is None

    def test_reference_index(self):
        documents = [
            build("a", number="2026-001"),
            build("b", number="2015-018"),
            build("c"),  # sans numéro : absent de l'index
        ]
        index = build_reference_index(documents)
        assert index == {("loi", "2026-001"): "a", ("loi", "2015-018"): "b"}


# ---------------------------------------------------------------------------
# Détection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_abrogation_is_detected(self, config):
        document = build(
            "loi_b",
            "Article 12 : La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        relations = detect_relations(document, config)
        assert len(relations) == 1
        assert relations[0].relation is RelationType.ABROGE
        assert "2015-018" in relations[0].target_reference

    def test_modification_is_detected(self, config):
        document = build(
            "loi_b",
            "Loi modifiant et completant la loi n° 2015-018 portant code du travail.",
            number="2026-002",
        )
        relations = detect_relations(document, config)
        assert any(r.relation is RelationType.MODIFIE for r in relations)

    def test_vu_clause_is_a_mere_citation(self, config):
        document = build("decret", "Vu la loi n° 2015-018 du 3 mars 2015 ;", number="2026-003")
        relations = detect_relations(document, config)
        citations = [r for r in relations if r.relation is RelationType.CITE]
        assert citations
        assert citations[0].confidence < 0.80, "une citation n'emporte pas d'effet juridique"

    def test_excerpt_and_page_are_kept_for_review(self, config):
        document = build(
            "loi_b",
            "Article 12 : La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        relation = detect_relations(document, config)[0]
        assert relation.page == 1
        assert "abroge" in relation.excerpt
        assert relation.article_id == "loi_b_article_1"

    def test_no_relation_in_a_plain_text(self, config):
        document = build("loi", "Article 1er : Le contrat de travail est conclu librement.")
        assert detect_relations(document, config) == []

    def test_detection_can_be_disabled(self, config):
        cfg = config.with_overrides({"relations": {"detect": False}})
        document = build("loi_b", "abroge la loi n° 2015-018", number="2026-001")
        report = annotate_relations([document], cfg)
        assert document.relations == []
        assert report.warnings


# ---------------------------------------------------------------------------
# Résolution
# ---------------------------------------------------------------------------


class TestResolution:
    def test_target_is_resolved_within_the_batch(self, config):
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.EN_VIGUEUR)
        nouvelle = build(
            "loi_2026",
            "Article 12 : La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        report = annotate_relations([ancienne, nouvelle], config)
        assert report.resolved == 1
        relation = nouvelle.relations[0]
        assert relation.target_document_id == "loi_2015"
        assert relation.needs_review is False

    def test_abrogation_propagates_the_status(self, config):
        """Loi A abrogée par Loi B (§13)."""
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.EN_VIGUEUR)
        nouvelle = build(
            "loi_2026",
            "Article 12 : La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        annotate_relations([ancienne, nouvelle], config)
        assert ancienne.metadata.status is LegalStatus.ABROGE
        assert ancienne.metadata.evidence["status"].startswith("loi_2026")
        assert any("à confirmer par un juriste" in w for w in ancienne.metadata.warnings)

    def test_modification_propagates_the_status(self, config):
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.EN_VIGUEUR)
        nouvelle = build(
            "loi_2026",
            "Loi modifiant et completant la loi n° 2015-018 portant code du travail.",
            number="2026-002",
        )
        annotate_relations([ancienne, nouvelle], config)
        assert ancienne.metadata.status is LegalStatus.MODIFIE

    def test_most_severe_status_wins(self, config):
        """Un texte modifié puis abrogé est abrogé."""
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.EN_VIGUEUR)
        modif = build("loi_a", "Loi modifiant la loi n° 2015-018 du 3 mars.", number="2020-001")
        abro = build("loi_b", "La presente loi abroge la loi n° 2015-018 du 3 mars.", number="2026-001")
        annotate_relations([ancienne, modif, abro], config)
        assert ancienne.metadata.status is LegalStatus.ABROGE

    def test_status_never_regresses(self, config):
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.ABROGE)
        modif = build("loi_a", "Loi modifiant la loi n° 2015-018 du 3 mars.", number="2020-001")
        annotate_relations([ancienne, modif], config)
        assert ancienne.metadata.status is LegalStatus.ABROGE

    def test_unresolved_target_is_kept_and_flagged(self, config):
        """L'information n'est pas jetée : elle attend le texte cible."""
        nouvelle = build(
            "loi_2026",
            "Article 12 : La presente loi abroge la loi n° 1999-007 du 3 mars 1999.",
            number="2026-001",
        )
        report = annotate_relations([nouvelle], config)
        assert report.unresolved == 1
        relation = nouvelle.relations[0]
        assert relation.target_document_id is None
        assert relation.target_reference
        assert relation.needs_review is True

    def test_vague_abrogation_never_changes_a_status(self, config):
        """« abroge toutes dispositions antérieures contraires » ne vise personne."""
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.EN_VIGUEUR)
        nouvelle = build(
            "loi_2026",
            "Article 12 : Sont abrogees toutes dispositions anterieures contraires.",
            number="2026-001",
        )
        annotate_relations([ancienne, nouvelle], config)
        assert ancienne.metadata.status is LegalStatus.EN_VIGUEUR

    def test_self_reference_is_rejected(self, config):
        document = build(
            "loi_2026",
            "La presente loi abroge la loi n° 2026-001 du 10 fevrier 2026.",
            number="2026-001",
        )
        report = annotate_relations([document], config)
        assert report.unresolved == 1
        assert any("lui-même" in w for w in report.warnings)
        assert document.metadata.status is LegalStatus.INCONNU

    def test_target_outside_the_batch_is_flagged_not_modified(self, config):
        nouvelle = build(
            "loi_2026",
            "La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        nouvelle.relations = detect_relations(nouvelle, config)
        report = resolve_relations(
            [nouvelle],
            config,
            external_index={("loi", "2015-018"): "loi_deja_en_base"},
        )
        assert report.resolved == 1
        assert report.statuses_flagged == 1
        assert any("devrait être révisé" in w for w in report.warnings)

    def test_high_confidence_threshold_blocks_weak_relations(self, config):
        """Au-dessus du seuil configuré, aucune relation ne modifie un statut."""
        cfg = config.with_overrides({"relations": {"min_confidence": 0.99}})
        ancienne = build("loi_2015", number="2015-018", status=LegalStatus.EN_VIGUEUR)
        nouvelle = build(
            "loi_2026",
            "La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        report = annotate_relations([ancienne, nouvelle], cfg)
        assert ancienne.metadata.status is LegalStatus.EN_VIGUEUR
        assert report.statuses_flagged == 1
        assert any("vérification humaine" in w for w in ancienne.metadata.warnings)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


class TestVersions:
    def test_same_number_means_same_text(self):
        first = build("loi_v1", number="2026-001", date="2026-02-10")
        second = build("loi_v2", number="2026-001", date="2027-01-05")
        groups = group_versions([first, second])
        assert len(groups) == 1
        assert len(next(iter(groups.values()))) == 2

    def test_versions_are_numbered_chronologically(self):
        first = build("loi_v1", number="2026-001", date="2026-02-10")
        second = build("loi_v2", number="2026-001", date="2027-01-05")
        assert assign_versions([second, first]) == 2
        assert first.metadata.version == "1"
        assert second.metadata.version == "2"

    def test_multiple_versions_are_flagged(self):
        first = build("loi_v1", number="2026-001", date="2026-02-10")
        second = build("loi_v2", number="2026-001", date="2027-01-05")
        assign_versions([first, second])
        assert any("2 versions détectées" in w for w in first.metadata.warnings)

    def test_distinct_texts_are_not_grouped(self):
        assert group_versions([build("a", number="2026-001"), build("b", number="2026-002")]) == {}

    def test_documents_without_number_are_not_grouped(self):
        assert group_versions([build("a"), build("b")]) == {}


# ---------------------------------------------------------------------------
# Restitution
# ---------------------------------------------------------------------------


class TestOutputs:
    def test_relation_graph(self, config):
        ancienne = build("loi_2015", number="2015-018")
        nouvelle = build(
            "loi_2026",
            "La presente loi abroge la loi n° 2015-018 du 3 mars 2015.",
            number="2026-001",
        )
        annotate_relations([ancienne, nouvelle], config)
        graph = relation_graph([ancienne, nouvelle])
        assert graph["loi_2026"][0]["target_document_id"] == "loi_2015"
        assert graph["loi_2026"][0]["relation"] == "abroge"

    def test_unresolved_queue_is_sorted_by_confidence(self, config):
        document = build(
            "loi_2026",
            "Vu la loi n° 1999-007 ; la presente loi abroge la loi n° 1990-003 du 2 mai.",
            number="2026-001",
        )
        annotate_relations([document], config)
        pending = unresolved_relations([document])
        assert pending
        assert all(r.needs_review for r in pending)
        assert pending == sorted(pending, key=lambda r: r.confidence)
