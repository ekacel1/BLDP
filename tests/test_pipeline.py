"""Tests du pipeline complet et de la CLI (§21, §26, §31).

Ce fichier vérifie surtout les **critères de réussite du MVP** (§31) : recevoir
un dossier de PDF, décider de l'OCR, extraire, nettoyer, structurer, produire
métadonnées, anomalies, rapport qualité, exports, base SQLite — et pouvoir
retrouver un article et sa page source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bldp.cli import main
from bldp.core.storage.sqlite_store import LegalDatabase
from bldp.models import QualityStatus, ValidationStatus
from bldp.pipeline import load_processed, process_source, process_only, run_pipeline
from bldp.utils import read_json


@pytest.fixture
def corpus(tmp_path, make_text_pdf):
    """Un petit corpus de trois lois fictives dans un dossier d'entrée."""
    folder = tmp_path / "corpus"
    folder.mkdir()

    pages = {
        "loi_travail.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
            "portant organisation du travail\n\n"
            "TITRE PREMIER\nDISPOSITIONS GENERALES\n\n"
            "CHAPITRE I\nDE L'OBJET\n\n"
            "Article 1er : La presente loi fixe les regles applicables aux relations "
            "de travail entre employeurs et travailleurs.\n\n"
            "Article 2 : Est considere comme travailleur toute personne physique qui "
            "s'engage a mettre son activite sous la direction d'autrui.\n",
            "CHAPITRE II\nDU CONTRAT DE TRAVAIL\n\n"
            "Section 1\nDe la formation du contrat\n\n"
            "Article 3 : Le contrat de travail est conclu librement entre les parties.\n\n"
            "Article 4 : Le contrat a duree determinee ne peut exceder quatre ans, "
            "renouvellement compris.\n",
        ],
        "decret_application.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "DECRET N° 2026-113 DU 4 MARS 2026\n"
            "portant application de la loi sur le travail\n\n"
            "Article 1er : Le present decret fixe les modalites d'application de la "
            "loi n° 2026-001 du 10 fevrier 2026.\n\n"
            "Article 2 : Les employeurs disposent d'un delai de six mois pour se "
            "conformer aux dispositions de la presente loi.\n"
        ],
        "loi_abrogeante.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2027-004 DU 2 JANVIER 2027\n"
            "portant abrogation de dispositions anciennes\n\n"
            "Article 1er : La presente loi abroge la loi n° 2026-001 du 10 fevrier 2026 "
            "portant organisation du travail.\n\n"
            "Article 2 : Elle entre en vigueur des sa publication au Journal Officiel.\n"
        ],
    }

    for name, content in pages.items():
        source = make_text_pdf(name, content)
        Path(source).replace(folder / name)
    return folder


# ---------------------------------------------------------------------------
# Critères de réussite du MVP (§31)
# ---------------------------------------------------------------------------


class TestMvpCriteria:
    def test_1_accepts_a_folder_of_pdfs(self, corpus, config):
        result = run_pipeline(corpus, config)
        assert result.report.total == 3

    def test_2_and_3_decides_ocr_and_extracts(self, corpus, config):
        result = run_pipeline(corpus, config)
        for document in result.documents:
            assert document.analysis is not None
            assert document.analysis.ocr_required is False, "PDF natifs : pas d'OCR"
            assert document.analysis.reasons, "la décision est toujours motivée"
            assert document.pages

    def test_4_cleans_artifacts(self, corpus, config):
        result = run_pipeline(corpus, config)
        document = next(d for d in result.documents if d.document_id == "loi_travail")
        assert all(page.raw_text is not None for page in document.pages), "le brut est gardé"

    def test_5_and_6_identifies_articles_with_hierarchy(self, corpus, config):
        result = run_pipeline(corpus, config)
        document = next(d for d in result.documents if d.document_id == "loi_travail")
        assert [a.article_number for a in document.articles] == ["1er", "2", "3", "4"]
        assert document.articles[0].title == "TITRE PREMIER"
        assert document.articles[0].chapter == "CHAPITRE I"
        assert document.articles[2].chapter == "CHAPITRE II"
        assert document.articles[2].section == "Section 1"

    def test_7_generates_metadata(self, corpus, config):
        result = run_pipeline(corpus, config)
        document = next(d for d in result.documents if d.document_id == "loi_travail")
        assert document.metadata.number == "2026-001"
        assert document.metadata.date == "2026-02-10"
        assert document.metadata.type.value == "loi"
        assert document.metadata.confidence, "chaque valeur devinée est chiffrée"

    def test_8_and_9_detects_anomalies_and_reports_quality(self, corpus, config):
        result = run_pipeline(corpus, config)
        for document in result.documents:
            assert document.quality is not None
            assert 0.0 <= document.quality.score <= 1.0
            assert document.quality.status in set(QualityStatus)

    def test_10_and_11_exports_jsonl_and_sqlite(self, corpus, config):
        result = run_pipeline(corpus, config)
        assert Path(result.exports["documents_jsonl"]).exists()
        assert Path(result.exports["articles_jsonl"]).exists()
        assert Path(result.exports["sqlite"]).exists()

        lines = Path(result.exports["articles_jsonl"]).read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 8
        assert json.loads(lines[0])["article_number"]

    def test_12_embeddings_are_optional(self, corpus, config):
        """Le corpus doit être complet sans jamais charger de modèle."""
        result = run_pipeline(corpus, config)
        assert result.embeddings_count == 0
        assert result.documents and result.exports

    def test_13_article_traces_back_to_its_page(self, corpus, config):
        """§31.13 : retrouver l'article original et sa page source."""
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            article = database.get_articles("loi_travail")[2]
            trace = database.trace_article(article["article_id"])
            assert trace["article"]["article_number"] == "3"
            assert trace["page"]["page"] == 2
            assert "Article 3" in trace["page"]["text"]
            assert trace["source_path"].endswith("loi_travail.pdf")

    def test_14_and_15_runs_locally_without_paid_api(self, config):
        assert config.get("privacy.allow_external_calls") is False
        assert config.get("embeddings.enabled") is False
        assert config.get("vectorstore.enabled") is False


# ---------------------------------------------------------------------------
# Robustesse (§26)
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    def test_a_broken_file_does_not_stop_the_others(self, corpus, config):
        """§26 : 100 documents, 96 réussis, 2 à vérifier, 2 échoués."""
        (corpus / "casse.pdf").write_bytes(b"ceci n'est pas un PDF")
        result = run_pipeline(corpus, config)

        assert result.report.total == 4
        assert result.report.failed >= 1
        assert len(result.documents) == 4, "le document en échec reste dans le corpus"
        broken = next(d for d in result.documents if d.document_id == "casse")
        assert broken.errors
        good = [d for d in result.documents if d.document_id != "casse"]
        assert all(d.articles for d in good), "les autres sont traités normalement"

    def test_counters_are_mutually_exclusive(self, corpus, config):
        (corpus / "casse.pdf").write_bytes(b"pas un pdf")
        result = run_pipeline(corpus, config)
        report = result.report
        assert report.succeeded + report.review_required + report.failed == report.total
        failed_ids = {d.document_id for d in result.failed}
        review_ids = {d.document_id for d in result.review_required}
        assert not (failed_ids & review_ids), "un document n'est jamais compté deux fois"

    def test_run_report_is_written(self, corpus, config):
        result = run_pipeline(corpus, config)
        report_path = Path(result.exports["run_report"])
        assert report_path.exists()
        payload = read_json(report_path)
        assert payload["total"] == 3
        assert "documents" in payload

    def test_pipeline_summary_is_written(self, corpus, config):
        run_pipeline(corpus, config)
        summary = read_json(config.path("exports") / "pipeline_summary.json")
        assert summary["documents"]["total"] == 3
        assert "quality" in summary and "duplicates" in summary

    def test_empty_input_is_not_an_error(self, tmp_path, config):
        empty = tmp_path / "vide"
        empty.mkdir()
        result = run_pipeline(empty, config)
        assert result.report.total == 0
        assert result.documents == []

    def test_unexpected_exception_is_caught(self, corpus, config, monkeypatch):
        import bldp.pipeline as pipeline_module

        original = pipeline_module.process_source
        calls = {"n": 0}

        def flaky(source, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("panne inattendue")
            return original(source, *args, **kwargs)

        monkeypatch.setattr(pipeline_module, "process_source", flaky)
        result = run_pipeline(corpus, config)
        assert result.report.total == 3
        assert result.report.failed == 1
        assert any("panne inattendue" in e["error"] for e in result.report.errors)


# ---------------------------------------------------------------------------
# Intégration entre modules
# ---------------------------------------------------------------------------


class TestCrossModule:
    def test_relations_are_detected_and_resolved(self, corpus, config):
        result = run_pipeline(corpus, config)
        abrogeante = next(d for d in result.documents if d.document_id == "loi_abrogeante")
        assert abrogeante.relations, "l'abrogation citée doit être relevée"
        resolved = [r for r in abrogeante.relations if r.target_document_id]
        assert resolved, "la cible est dans le corpus, elle doit être résolue"
        assert resolved[0].target_document_id == "loi_travail"

        cible = next(d for d in result.documents if d.document_id == "loi_travail")
        assert cible.metadata.status.value == "abroge"
        assert any("juriste" in w for w in cible.metadata.warnings)

    def test_duplicates_are_flagged_not_removed(self, corpus, config):
        import shutil

        shutil.copy(corpus / "loi_travail.pdf", corpus / "copie_loi_travail.pdf")
        result = run_pipeline(corpus, config)
        assert len(result.documents) == 4, "le doublon reste dans le corpus"
        flagged = [d for d in result.documents if d.duplicates]
        assert flagged
        assert flagged[0].duplicates[0].kind in {"file_hash", "text_hash"}

    def test_chunks_are_produced_even_without_embeddings(self, corpus, config):
        result = run_pipeline(corpus, config)
        assert result.chunks
        assert all(chunk.document_id for chunk in result.chunks)
        assert all(chunk.text.strip() for chunk in result.chunks)

    def test_second_run_is_idempotent(self, corpus, config):
        run_pipeline(corpus, config)
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            stats = database.stats()
        assert stats["counts"]["documents"] == 3, "pas de duplication en base"

    def test_human_validation_survives_a_second_run(self, corpus, config):
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            database.set_validation("loi_travail", ValidationStatus.VALIDATED, "vérifié")
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            assert database.get_document_row("loi_travail")["validation"] == "valide"


class TestProcessOnly:
    def test_writes_one_json_per_document(self, corpus, config):
        result = process_only(corpus, config)
        files = sorted(config.path("processed").glob("*.json"))
        assert len(files) == 3
        assert not result.exports.get("sqlite")

    def test_processed_documents_can_be_reread(self, corpus, config):
        process_only(corpus, config)
        documents = load_processed(config.path("processed"))
        assert len(documents) == 3
        assert all("articles" in d for d in documents)

    def test_limit(self, corpus, config):
        result = process_only(corpus, config, limit=1)
        assert result.report.total == 1


# ---------------------------------------------------------------------------
# CLI (§21)
# ---------------------------------------------------------------------------


class TestCli:
    def test_help_exits_cleanly(self, capsys):
        assert main([]) == 0
        assert "COMMANDE" in capsys.readouterr().out

    def test_all_spec_commands_exist(self):
        from bldp.cli import build_parser

        parser = build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        commands = set()
        for action in actions:
            commands.update(action.choices or {})
        assert {"ingest", "process", "validate", "embed", "export", "pipeline"} <= commands

    def test_config_command(self, capsys):
        assert main(["config", "ocr.language"]) == 0
        assert capsys.readouterr().out.strip() == "fra"

    def test_unknown_config_key_fails(self, capsys):
        assert main(["config", "section.inexistante"]) == 1

    def test_doctor_runs(self, capsys):
        assert main(["doctor"]) == 0
        assert "diagnostic" in capsys.readouterr().out

    def test_bad_override_is_rejected(self, capsys):
        assert main(["config", "--set", "sans_egal"]) == 1

    def test_pipeline_end_to_end(self, corpus, tmp_path, capsys):
        assert main(["pipeline", str(corpus), "--root", str(tmp_path)]) == 0
        output = capsys.readouterr().out
        assert "3 document(s) traité(s)" in output
        assert "documents_jsonl" in output

    def test_stats_without_database_is_explicit(self, tmp_path, capsys):
        code = main(["stats", "--set", f"paths.database={tmp_path}/absent.sqlite"])
        assert code == 1
        assert "Aucune base" in capsys.readouterr().err

    def test_trace_unknown_article(self, corpus, tmp_path, capsys):
        main(["pipeline", str(corpus), "--root", str(tmp_path)])
        database = tmp_path / "data" / "exports" / "legal_database.sqlite"
        code = main(["trace", "inexistant", "--set", f"paths.database={database}"])
        assert code == 1

    def test_analyze_reports_route(self, corpus, capsys):
        assert main(["analyze", str(corpus)]) == 0
        output = capsys.readouterr().out
        assert "native" in output

    def test_root_option_keeps_the_repository_clean(self, corpus, tmp_path):
        """``--root`` doit vraiment détourner les écritures hors du dépôt.

        Régression : ``load_config`` écrasait la racine demandée, si bien que
        les exécutions de test écrivaient dans le ``data/`` du projet.
        """
        assert main(["pipeline", str(corpus), "--root", str(tmp_path), "-q"]) == 0
        assert (tmp_path / "data" / "exports" / "legal_database.sqlite").exists()
        assert (tmp_path / "data" / "exports" / "documents.jsonl").exists()

    def test_citation_does_not_requalify_the_document(self, corpus, config):
        """Un décret citant une loi reste un décret (§12)."""
        result = run_pipeline(corpus, config)
        decret = next(d for d in result.documents if d.document_id == "decret_application")
        assert decret.metadata.type.value == "decret"
        assert decret.metadata.number == "2026-113"


class TestEmbeddingsBranch:
    """La branche optionnelle du pipeline doit dégrader proprement (§19, §26)."""

    def test_missing_dependency_leaves_the_corpus_complete(self, corpus, config, caplog):
        from bldp.core import embeddings as embeddings_module

        cfg = config.with_overrides({"embeddings": {"enabled": True}})
        with caplog.at_level("WARNING"):
            result = run_pipeline(corpus, cfg, do_embeddings=True)

        if not embeddings_module.embeddings_available():
            assert result.embeddings_count == 0
            assert any("Embeddings ignorés" in r.message for r in caplog.records)
        # Dans les deux cas, le corpus reste exploitable.
        assert result.chunks
        assert Path(result.exports["articles_jsonl"]).exists()

    def test_embeddings_are_persisted_when_available(self, corpus, config, monkeypatch):
        """Avec un modèle disponible, vecteurs et fragments sont enregistrés."""
        from bldp.core import embeddings as embeddings_module
        from bldp.models import EmbeddingRecord

        def fake_check(cfg):
            return True, []

        def fake_embed(chunks, cfg, model=None, show_progress=False):
            return [
                EmbeddingRecord(
                    vector_id=f"{chunk.chunk_id}_vec",
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    embedding_model="modele-de-test",
                    dimension=3,
                    article_id=chunk.article_id,
                    article_number=chunk.article_number,
                    text=chunk.text,
                    vector=[0.1, 0.2, 0.3],
                )
                for chunk in chunks
            ]

        monkeypatch.setattr(embeddings_module, "check_embeddings_ready", fake_check)
        monkeypatch.setattr(embeddings_module, "embed_chunks", fake_embed)

        cfg = config.with_overrides({"embeddings": {"enabled": True}})
        result = run_pipeline(corpus, cfg, do_embeddings=True)

        assert result.embeddings_count == len(result.chunks) > 0
        # L'index vectoriel reste facultatif : son absence n'est pas un échec.
        with LegalDatabase(cfg.path("database"), create=False) as database:
            stats = database.stats()
        assert stats["counts"]["chunks"] == len(result.chunks)
        assert stats["counts"]["embeddings"] == result.embeddings_count

    def test_embedding_failure_does_not_lose_the_corpus(self, corpus, config, monkeypatch):
        from bldp.core import embeddings as embeddings_module

        def boom(chunks, cfg, model=None, show_progress=False):
            raise embeddings_module.EmbeddingError("modèle introuvable")

        monkeypatch.setattr(embeddings_module, "check_embeddings_ready", lambda cfg: (True, []))
        monkeypatch.setattr(embeddings_module, "embed_chunks", boom)

        cfg = config.with_overrides({"embeddings": {"enabled": True}})
        result = run_pipeline(corpus, cfg, do_embeddings=True)

        assert result.embeddings_count == 0
        assert result.chunks, "les fragments restent produits"
        assert Path(result.exports["sqlite"]).exists()


class TestCliIntegrity:
    """Garde-fou : chaque commande déclarée doit avoir son gestionnaire.

    Une suppression un peu large dans `cli.py` a déjà fait disparaître
    `cmd_serve` : le parseur se construisait, mais la commande explosait à
    l'exécution. Ce test rend la classe d'erreur impossible.
    """

    def test_every_command_has_a_handler(self):
        import ast
        import re
        from pathlib import Path

        source = Path("bldp/cli.py").read_text(encoding="utf-8")
        definies = {
            node.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)
        }
        referencees = set(re.findall(r"set_defaults\(func=(\w+)\)", source))
        assert referencees, "aucune commande déclarée ?"
        assert referencees <= definies, f"gestionnaires manquants : {referencees - definies}"

    def test_the_parser_builds_without_error(self):
        from bldp.cli import build_parser

        assert build_parser() is not None


# ---------------------------------------------------------------------------
# Repli OCR : une extraction muette est une extraction ratée
# ---------------------------------------------------------------------------


class TestRepliOcr:
    """Le cas le plus coûteux du corpus, et le seul qui se répare gratuitement.

    Mesuré sur le lot 1 du corpus SGG : 559 documents sans aucun article, dont
    552 passés par la voie native. Leur PDF porte une couche texte issue d'un
    OCR d'époque, du genre ``ARTICIS Ier`` ou ``A,rUi:cfJE 5.-``. Le
    classifieur voit du texte, conclut « OCR non nécessaire », et le contenu
    reste sur l'image.
    """

    def _document(self, articles, warnings=None):
        from bldp.models import DocumentMetadata

        doc = type("D", (), {})()
        doc.document_id = "loi_test"
        doc.metadata = DocumentMetadata(document_id="loi_test", warnings=warnings or [])
        doc.articles = articles
        doc.structure = []
        doc.errors = []
        doc.extraction = None
        doc.text_hash = ""
        return doc

    def _analyse(self, articles):
        return type("P", (), {"articles": articles, "structure": [], "warnings": []})()

    def test_un_document_avec_articles_n_est_pas_relu(self, config, monkeypatch):
        """Le repli ne coûte rien quand il ne sert à rien."""
        import bldp.pipeline as pipeline_module

        appels = []
        monkeypatch.setattr(
            pipeline_module, "extract_with_route",
            lambda *a, **k: appels.append(1),
        )
        origine = self._analyse(["article 1"])
        pages, resultat = pipeline_module._repli_ocr_si_aucun_article(
            self._document(["article 1"]), _source(), Path("/x.pdf"), "native",
            config, None, None, ["pages"], origine,
        )
        assert appels == [], "aucune seconde lecture ne doit être tentée"
        assert resultat is origine

    def test_un_document_deja_ocrise_n_est_pas_relu_deux_fois(self, config, monkeypatch):
        import bldp.pipeline as pipeline_module

        appels = []
        monkeypatch.setattr(
            pipeline_module, "extract_with_route",
            lambda *a, **k: appels.append(1),
        )
        pipeline_module._repli_ocr_si_aucun_article(
            self._document([]), _source(), Path("/x.pdf"), "ocr",
            config, None, None, ["pages"], self._analyse([]),
        )
        assert appels == [], "un document déjà OCRisé ne se relit pas en boucle"

    def test_le_repli_se_desactive(self, config, monkeypatch):
        import bldp.pipeline as pipeline_module

        appels = []
        monkeypatch.setattr(
            pipeline_module, "extract_with_route",
            lambda *a, **k: appels.append(1),
        )
        conf = config.with_overrides(
            {"extraction": {"ocr_fallback_when_no_article": False}}
        )
        pipeline_module._repli_ocr_si_aucun_article(
            self._document([]), _source(), Path("/x.pdf"), "native",
            conf, None, None, ["pages"], self._analyse([]),
        )
        assert appels == []

    def test_une_seconde_lecture_meilleure_est_retenue(self, config, monkeypatch):
        """Le cas nominal : l'OCR récupère ce que la couche texte cachait."""
        import bldp.pipeline as pipeline_module

        extraction = type("E", (), {"pages": ["p1"], "errors": []})()
        monkeypatch.setattr(
            pipeline_module, "extract_with_route", lambda *a, **k: extraction
        )
        monkeypatch.setattr(
            pipeline_module, "clean_pages", lambda *a, **k: (["p1 propre"], None)
        )
        monkeypatch.setattr(
            pipeline_module, "parse_document",
            lambda *a, **k: self._analyse(["art 1", "art 2", "art 3"]),
        )
        monkeypatch.setattr(pipeline_module, "document_text_hash", lambda d: "h")

        document = self._document([])
        pages, resultat = pipeline_module._repli_ocr_si_aucun_article(
            document, _source(), Path("/x.pdf"), "native",
            config, None, None, ["ancien"], self._analyse([]),
        )
        assert len(resultat.articles) == 3
        assert document.articles == ["art 1", "art 2", "art 3"]
        assert pages == ["p1 propre"]
        assert any("relu en OCR" in w for w in document.metadata.warnings)

    def test_une_seconde_lecture_moins_bonne_est_ecartee(self, config, monkeypatch):
        """Un OCR ne doit jamais écraser un texte natif qui faisait mieux."""
        import bldp.pipeline as pipeline_module

        monkeypatch.setattr(
            pipeline_module, "extract_with_route",
            lambda *a, **k: type("E", (), {"pages": ["p"], "errors": []})(),
        )
        monkeypatch.setattr(pipeline_module, "clean_pages", lambda *a, **k: (["p"], None))
        monkeypatch.setattr(
            pipeline_module, "parse_document", lambda *a, **k: self._analyse([])
        )

        document = self._document([])
        origine = self._analyse([])
        pages, resultat = pipeline_module._repli_ocr_si_aucun_article(
            document, _source(), Path("/x.pdf"), "native",
            config, None, None, ["ancien"], origine,
        )
        assert resultat is origine
        assert pages == ["ancien"]
        assert any("y compris après une seconde lecture" in w
                   for w in document.metadata.warnings)

    def test_un_echec_de_seconde_lecture_laisse_le_document_intact(
        self, config, monkeypatch
    ):
        """§26 : le repli est un supplément, jamais une condition."""
        import bldp.pipeline as pipeline_module
        from bldp.core.extraction.pymupdf_extractor import ExtractionError

        def casse(*a, **k):
            raise ExtractionError("tesseract absent")

        monkeypatch.setattr(pipeline_module, "extract_with_route", casse)
        document = self._document([])
        origine = self._analyse([])
        pages, resultat = pipeline_module._repli_ocr_si_aucun_article(
            document, _source(), Path("/x.pdf"), "native",
            config, None, None, ["ancien"], origine,
        )
        assert resultat is origine
        assert pages == ["ancien"]
        assert any("repli OCR impossible" in w for w in document.metadata.warnings)

    def test_sans_ocr_disponible_le_repli_ne_tente_rien(self, config, monkeypatch):
        import bldp.pipeline as pipeline_module

        appels = []
        monkeypatch.setattr(
            pipeline_module, "extract_with_route", lambda *a, **k: appels.append(1)
        )
        conf = config.with_overrides({"ocr": {"enabled": False}})
        pipeline_module._repli_ocr_si_aucun_article(
            self._document([]), _source(), Path("/x.pdf"), "native",
            conf, None, None, ["pages"], self._analyse([]),
        )
        assert appels == []


def _source():
    from bldp.models import SourceFile
    from bldp.utils import utc_now_iso

    return SourceFile(
        document_id="loi_test", source_path="/x.pdf", filename="x.pdf",
        extension=".pdf", size_bytes=1, file_hash="abc", ingested_at=utc_now_iso(),
    )
