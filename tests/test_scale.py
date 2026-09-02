"""Tests des fonctions de passage à l'échelle : reprise, parallélisme, rétention.

Ces trois mécanismes ont un point commun : ils **modifient ce qui est traité ou
conservé**. Une erreur y est donc silencieuse par nature — un document sauté à
tort, un export tronqué, un PDF supprimé qu'il fallait garder. Les tests
ci-dessous portent d'abord sur ces risques.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bldp.core.storage.sqlite_store import LegalDatabase, load_document, load_documents
from bldp.models import ExtractionMethod, QualityStatus, ValidationStatus
from bldp.pipeline import (
    _partition_sources,
    _resolve_workers,
    load_completed_hashes,
    process_only,
    purge_ocr_pdfs,
    run_pipeline,
)


@pytest.fixture
def corpus(tmp_path, make_text_pdf):
    """Quatre lois fictives, dont une hiérarchisée."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    textes = {
        "loi_a.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
            "portant organisation du travail\n\n"
            "TITRE PREMIER\nDISPOSITIONS GENERALES\n\n"
            "CHAPITRE I\nDE L'OBJET\n\n"
            "Article 1er : La presente loi fixe les regles applicables.\n\n"
            "Article 2 : Est considere comme travailleur toute personne physique.\n"
        ],
        "loi_b.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-002 DU 11 FEVRIER 2026\n"
            "portant statut de la fonction publique\n\n"
            "Article 1er : Le present texte fixe le statut des agents publics.\n"
        ],
        "loi_c.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-003 DU 12 FEVRIER 2026\n"
            "portant organisation des marches publics\n\n"
            "Article 1er : Les marches publics obeissent aux regles suivantes.\n"
        ],
        "loi_d.pdf": [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-004 DU 13 FEVRIER 2026\n"
            "portant protection de l'environnement\n\n"
            "Article 1er : La protection de l'environnement est d'interet general.\n"
        ],
    }
    for nom, pages in textes.items():
        Path(make_text_pdf(nom, pages)).replace(folder / nom)
    return folder


# ---------------------------------------------------------------------------
# Reprise incrémentale
# ---------------------------------------------------------------------------


class TestResume:
    def test_a_second_run_skips_everything(self, corpus, config):
        run_pipeline(corpus, config)
        second = run_pipeline(corpus, config, resume=True)
        assert len(second.skipped_existing) == 4
        assert second.report.total == 0
        assert second.documents == []

    def test_only_new_documents_are_processed(self, corpus, config, make_text_pdf):
        run_pipeline(corpus, config)
        Path(make_text_pdf("loi_e.pdf", [
            "LOI N° 2026-005 DU 14 FEVRIER 2026 portant dispositions diverses\n"
            "Article 1er : Disposition nouvelle du present texte.\n"
        ])).replace(corpus / "loi_e.pdf")

        second = run_pipeline(corpus, config, resume=True)
        assert second.report.total == 1
        assert [d.document_id for d in second.documents] == ["loi_e"]
        assert len(second.skipped_existing) == 4

    def test_the_exports_still_describe_the_whole_corpus(self, corpus, config, make_text_pdf):
        """Le piège de la reprise : ne pas tronquer les exports au dernier lot."""
        run_pipeline(corpus, config)
        Path(make_text_pdf("loi_e.pdf", [
            "LOI N° 2026-005 DU 14 FEVRIER 2026 portant dispositions diverses\n"
            "Article 1er : Disposition nouvelle du present texte.\n"
        ])).replace(corpus / "loi_e.pdf")

        second = run_pipeline(corpus, config, resume=True)
        lignes = Path(second.exports["documents_jsonl"]).read_text(
            encoding="utf-8"
        ).splitlines()
        identifiants = {json.loads(l)["document_id"] for l in lignes}
        assert identifiants == {"loi_a", "loi_b", "loi_c", "loi_d", "loi_e"}, (
            "les exports doivent refléter tout le corpus, pas seulement le lot"
        )

    def test_nothing_new_still_regenerates_the_exports(self, corpus, config):
        run_pipeline(corpus, config)
        second = run_pipeline(corpus, config, resume=True)
        assert second.report.total == 0
        chemin = Path(second.exports["documents_jsonl"])
        assert chemin.exists()
        assert len(chemin.read_text(encoding="utf-8").splitlines()) == 4

    def test_a_failed_document_is_retried(self, corpus, config):
        """Une panne transitoire ne doit pas devenir une perte définitive."""
        (corpus / "casse.pdf").write_bytes(b"pas un pdf")
        premier = run_pipeline(corpus, config)
        assert premier.report.failed == 1

        second = run_pipeline(corpus, config, resume=True)
        assert [d.document_id for d in second.documents] == ["casse"], (
            "le document en échec doit être retenté, pas ignoré"
        )

    def test_resume_is_off_by_default(self, corpus, config):
        run_pipeline(corpus, config)
        second = run_pipeline(corpus, config)
        assert second.skipped_existing == []
        assert second.report.total == 4

    def test_completed_hashes_exclude_failures(self, corpus, config):
        (corpus / "casse.pdf").write_bytes(b"pas un pdf")
        run_pipeline(corpus, config)
        completed = load_completed_hashes(config)
        assert "casse" not in completed.values()
        assert len(completed) == 4

    def test_no_database_means_nothing_to_skip(self, config):
        assert load_completed_hashes(config) == {}

    def test_partition_is_stable(self):
        from bldp.models import SourceFile
        from bldp.utils import utc_now_iso

        def source(doc_id, digest):
            return SourceFile(
                document_id=doc_id, source_path=f"/{doc_id}.pdf",
                filename=f"{doc_id}.pdf", extension=".pdf", size_bytes=1,
                file_hash=digest, ingested_at=utc_now_iso(),
            )

        sources = [source("a", "h1"), source("b", "h2"), source("c", "h3")]
        reste, sautes = _partition_sources(sources, {"h2": "b"})
        assert [s.document_id for s in reste] == ["a", "c"]
        assert sautes == ["b"]

    def test_the_report_records_what_was_skipped(self, corpus, config):
        run_pipeline(corpus, config)
        second = run_pipeline(corpus, config, resume=True)
        assert sorted(second.report.skipped_existing) == ["loi_a", "loi_b", "loi_c", "loi_d"]


# ---------------------------------------------------------------------------
# Parallélisme
# ---------------------------------------------------------------------------


class TestWorkers:
    def test_parallel_gives_the_same_corpus_as_sequential(self, corpus, config, tmp_path):
        sequentiel = run_pipeline(corpus, config, workers=1)
        attendu = [
            (d.document_id, [a.article_number for a in d.articles]) for d in sequentiel.documents
        ]

        autre = config.with_overrides({"_root": str(tmp_path / "parallele")})
        autre.ensure_directories()
        parallele = run_pipeline(corpus, autre, workers=4)
        obtenu = [
            (d.document_id, [a.article_number for a in d.articles]) for d in parallele.documents
        ]

        assert obtenu == attendu, "le parallélisme ne doit rien changer au résultat"

    def test_order_follows_the_input_not_completion(self, corpus, config):
        result = run_pipeline(corpus, config, workers=4)
        assert [d.document_id for d in result.documents] == [
            "loi_a", "loi_b", "loi_c", "loi_d"
        ]

    def test_a_failure_in_one_thread_does_not_stop_the_others(self, corpus, config, monkeypatch):
        import bldp.pipeline as pipeline_module

        original = pipeline_module.process_source

        def flaky(source, *args, **kwargs):
            if source.document_id == "loi_c":
                raise RuntimeError("panne simulée")
            return original(source, *args, **kwargs)

        monkeypatch.setattr(pipeline_module, "process_source", flaky)
        result = run_pipeline(corpus, config, workers=4)
        assert result.report.total == 4
        assert result.report.failed == 1
        assert len(result.documents) == 4

    @pytest.mark.parametrize(
        "demande, documents, attendu",
        [(1, 10, 1), (4, 10, 4), (8, 3, 3), (-1, 10, None)],
    )
    def test_worker_count_resolution(self, config, demande, documents, attendu):
        import os

        obtenu = _resolve_workers(config, demande, documents)
        if attendu is None:
            assert obtenu == min(os.cpu_count() or 1, documents)
        else:
            assert obtenu == attendu

    def test_never_more_threads_than_documents(self, config):
        assert _resolve_workers(config, 16, 2) == 2

    def test_parallel_run_caps_ocrmypdf_internal_jobs(self, corpus, config, monkeypatch):
        """Deux niveaux de parallélisme se multiplient : il faut en brider un."""
        vus: list[int] = []
        import bldp.pipeline as pipeline_module

        original = pipeline_module.process_source

        def espion(source, cfg, *args, **kwargs):
            vus.append(int(cfg.get("ocr.jobs", 0)))
            return original(source, cfg, *args, **kwargs)

        monkeypatch.setattr(pipeline_module, "process_source", espion)
        run_pipeline(corpus, config, workers=4)
        assert vus and all(j == 1 for j in vus)

    def test_sequential_run_leaves_ocrmypdf_free(self, corpus, config, monkeypatch):
        vus: list[int] = []
        import bldp.pipeline as pipeline_module

        original = pipeline_module.process_source

        def espion(source, cfg, *args, **kwargs):
            vus.append(int(cfg.get("ocr.jobs", 0)))
            return original(source, cfg, *args, **kwargs)

        monkeypatch.setattr(pipeline_module, "process_source", espion)
        run_pipeline(corpus, config, workers=1)
        assert vus and all(j == 0 for j in vus)


# ---------------------------------------------------------------------------
# Rétention des PDF OCRisés
# ---------------------------------------------------------------------------


def _document_avec_pdf(tmp_path, doc_id, status, index):
    """Document factice portant un PDF OCRisé sur disque."""
    from bldp.models import (
        Document, DocumentMetadata, ExtractionResult, Page, QualityReport, SourceFile,
    )
    from bldp.utils import utc_now_iso

    pdf = tmp_path / f"{doc_id}_ocr.pdf"
    pdf.write_bytes(b"%PDF-1.4" + b"x" * 1000)
    extraction = ExtractionResult(
        document_id=doc_id, source_file=f"{doc_id}.pdf", method=ExtractionMethod.OCR,
        pages=[Page(document_id=doc_id, page=1, text="texte", source_file=f"{doc_id}.pdf")],
        ocr_pdf_path=str(pdf),
    )
    return Document(
        document_id=doc_id,
        source=SourceFile(
            document_id=doc_id, source_path=f"/{doc_id}.pdf", filename=f"{doc_id}.pdf",
            extension=".pdf", size_bytes=1, file_hash=str(index) * 64,
            ingested_at=utc_now_iso(),
        ),
        metadata=DocumentMetadata(document_id=doc_id),
        extraction=extraction,
        quality=QualityReport(document_id=doc_id, score=0.9, status=status),
    )


class TestOcrRetention:
    def test_all_keeps_everything(self, tmp_path):
        docs = [_document_avec_pdf(tmp_path, "a", QualityStatus.OK, 1)]
        assert purge_ocr_pdfs(docs, "all") == (0, 0)
        assert Path(docs[0].extraction.ocr_pdf_path).exists()

    def test_none_removes_everything(self, tmp_path):
        docs = [
            _document_avec_pdf(tmp_path, "a", QualityStatus.OK, 1),
            _document_avec_pdf(tmp_path, "b", QualityStatus.REVIEW_REQUIRED, 2),
        ]
        chemins = [Path(d.extraction.ocr_pdf_path) for d in docs]
        removed, freed = purge_ocr_pdfs(docs, "none")
        assert removed == 2 and freed > 0
        assert not any(c.exists() for c in chemins)

    def test_review_keeps_the_documents_a_human_must_check(self, tmp_path):
        """L'auditabilité est conservée là exactement où elle sert."""
        propre = _document_avec_pdf(tmp_path, "propre", QualityStatus.OK, 1)
        douteux = _document_avec_pdf(tmp_path, "douteux", QualityStatus.REVIEW_REQUIRED, 2)
        echoue = _document_avec_pdf(tmp_path, "echoue", QualityStatus.OK, 3)
        echoue.errors.append("extraction impossible")

        chemin_propre = Path(propre.extraction.ocr_pdf_path)
        removed, _ = purge_ocr_pdfs([propre, douteux, echoue], "review")

        assert removed == 1
        assert not chemin_propre.exists()
        assert Path(douteux.extraction.ocr_pdf_path).exists()
        assert Path(echoue.extraction.ocr_pdf_path).exists()

    def test_the_removal_is_recorded_in_the_document(self, tmp_path):
        """Une suppression doit rester traçable, pas silencieuse."""
        doc = _document_avec_pdf(tmp_path, "a", QualityStatus.OK, 1)
        purge_ocr_pdfs([doc], "none")
        assert doc.extraction.ocr_pdf_path is None
        assert any("rétention" in w for w in doc.extraction.warnings)

    def test_an_unknown_policy_keeps_everything(self, tmp_path, caplog):
        doc = _document_avec_pdf(tmp_path, "a", QualityStatus.OK, 1)
        with caplog.at_level("WARNING"):
            assert purge_ocr_pdfs([doc], "magique") == (0, 0)
        assert Path(doc.extraction.ocr_pdf_path).exists()

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        doc = _document_avec_pdf(tmp_path, "a", QualityStatus.OK, 1)
        Path(doc.extraction.ocr_pdf_path).unlink()
        assert purge_ocr_pdfs([doc], "none") == (0, 0)

    def test_policy_is_applied_by_the_pipeline(self, corpus, config):
        cfg = config.with_overrides({"ocr": {"keep_sidecar_for": "none"}})
        result = run_pipeline(corpus, cfg)
        # Aucun de ces PDF natifs n'a été OCRisé : rien à purger, et surtout
        # aucune erreur.
        assert result.purged_ocr_pdfs == 0
        assert result.documents


# ---------------------------------------------------------------------------
# Reconstruction depuis la base
# ---------------------------------------------------------------------------


class TestDocumentRebuilding:
    def test_everything_persisted_is_restored(self, corpus, config):
        """Une reconstruction partielle perdrait la hiérarchie en silence."""
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            document = load_document(database, "loi_a")

        assert document is not None
        assert document.metadata.number == "2026-001"
        assert document.pages and document.pages[0].raw_text is not None
        assert document.structure, "la structure hiérarchique doit être restaurée"
        assert document.articles and document.articles[0].hierarchy_path
        assert document.articles[0].alineas
        assert document.quality is not None and document.quality.issues is not None
        assert document.analysis is not None

    def test_validation_survives_the_rebuild(self, corpus, config):
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            database.set_validation("loi_a", ValidationStatus.VALIDATED, "vérifié")
            document = load_document(database, "loi_a")
        assert document.validation is ValidationStatus.VALIDATED
        assert document.validation_note == "vérifié"

    def test_unknown_document(self, corpus, config):
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            assert load_document(database, "inexistant") is None

    def test_rebuilt_exports_keep_the_structure(self, corpus, config):
        """Régression : les exports régénérés perdaient structure et relations."""
        run_pipeline(corpus, config)
        with LegalDatabase(config.path("database"), create=False) as database:
            documents = load_documents(database)

        from bldp.core.storage.exporters import document_record

        record = document_record(next(d for d in documents if d.document_id == "loi_a"))
        assert record["structure"], "la structure doit figurer dans l'export"
        assert record["article_count"] == 2
