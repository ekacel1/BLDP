"""Tests de la persistance et des exports (§17).

L'exigence structurante testée ici est la **traçabilité** : depuis un
``article_id``, on doit pouvoir remonter au document, à la page et au fichier
d'origine (§33).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bldp.core.storage.exporters import (
    article_record,
    document_record,
    export_all,
    export_articles_csv,
    export_articles_jsonl,
    export_documents_jsonl,
    export_metadata_json,
    export_quality_report_json,
    load_documents_jsonl,
)
from bldp.core.storage.sqlite_store import LegalDatabase, rebuild_metadata
from bldp.models import (
    Alinea,
    Article,
    Document,
    DocumentMetadata,
    DocumentType,
    DuplicateLink,
    ExtractionMethod,
    ExtractionResult,
    LegalRelation,
    LegalStatus,
    Page,
    PdfAnalysis,
    QualityIssue,
    QualityReport,
    QualityStatus,
    RelationType,
    SourceFile,
    StructureLevel,
    StructureNode,
    ValidationStatus,
)
from bldp.utils import utc_now_iso


# ---------------------------------------------------------------------------
# Fabrique de documents de test
# ---------------------------------------------------------------------------


def make_document(document_id: str = "loi_2026_001", articles: int = 2) -> Document:
    source = SourceFile(
        document_id=document_id,
        source_path=f"/input/lois/{document_id}.pdf",
        filename=f"{document_id}.pdf",
        extension=".pdf",
        size_bytes=12345,
        file_hash="f" * 64,
        ingested_at=utc_now_iso(),
        category="lois",
        raw_path=f"/data/raw/{document_id}.pdf",
    )
    metadata = DocumentMetadata(
        document_id=document_id,
        title="Loi portant organisation du travail",
        type=DocumentType.LOI,
        number="2026-001",
        date="2026-02-10",
        authority="Assemblée nationale",
        legal_domain="travail",
        source="SGG",
        status=LegalStatus.EN_VIGUEUR,
        confidence={"number": 0.92, "date": 0.95},
        evidence={"number": "LOI N° 2026-001", "date": "du 10 fevrier 2026"},
        warnings=["autorité émettrice introuvable"],
    )
    pages = [
        Page(
            document_id=document_id,
            page=index + 1,
            text=f"Texte nettoye de la page {index + 1}",
            raw_text=f"  Texte   BRUT de la page {index + 1}  ",
            source_file=source.filename,
            method=ExtractionMethod.NATIVE,
        )
        for index in range(2)
    ]
    structure = [
        StructureNode(
            node_id=f"{document_id}_titre_i",
            document_id=document_id,
            level=StructureLevel.TITRE,
            number="I",
            label="TITRE I",
            page=1,
        )
    ]
    article_list = [
        Article(
            article_id=f"{document_id}_article_{n}",
            document_id=document_id,
            article_number=str(n),
            text=f"Contenu de l'article {n}.",
            label=f"Article {n}",
            position=n - 1,
            page_start=1,
            page_end=1,
            numeric_value=float(n),
            title="TITRE I",
            hierarchy_path=["TITRE I"],
            alineas=[Alinea(index=0, text=f"Alinea unique de l'article {n}.")],
            source_file=source.filename,
        )
        for n in range(1, articles + 1)
    ]
    return Document(
        document_id=document_id,
        source=source,
        metadata=metadata,
        analysis=PdfAnalysis(
            document_id=document_id, pages=2, size_bytes=12345, has_text=True,
            ocr_required=False, confidence=0.95,
        ),
        extraction=ExtractionResult(
            document_id=document_id, source_file=source.filename,
            method=ExtractionMethod.NATIVE, pages=pages,
        ),
        structure=structure,
        articles=article_list,
        relations=[
            LegalRelation(
                relation_id=f"{document_id}_rel_1",
                source_document_id=document_id,
                relation=RelationType.ABROGE,
                target_reference="loi n° 2015-018",
                confidence=0.85,
                needs_review=False,
                excerpt="abroge la loi n° 2015-018",
            )
        ],
        duplicates=[
            DuplicateLink(
                document_id=document_id, duplicate_of="autre_doc",
                kind="text_hash", similarity=1.0, details="texte identique",
            )
        ],
        quality=QualityReport(
            document_id=document_id, score=0.94, text_quality=0.98,
            pages=2, articles_detected=articles, status=QualityStatus.OK,
            issues=[QualityIssue(code="page_vide", severity="warning", message="page 2 vide", page=2)],
        ),
        text_hash="a" * 64,
        processed_at=utc_now_iso(),
        pipeline_version="0.1.0",
    )


@pytest.fixture
def database(tmp_path):
    with LegalDatabase(tmp_path / "legal_database.sqlite") as db:
        yield db


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


class TestSchema:
    def test_all_tables_exist(self, database):
        rows = database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = {row["name"] for row in rows}
        assert {
            "documents", "pages", "structure", "articles", "alineas", "relations",
            "duplicates", "quality_reports", "quality_issues", "chunks", "embeddings",
        } <= tables

    def test_schema_version_is_recorded(self, database):
        assert database.schema_version() >= 1

    def test_creating_twice_is_safe(self, tmp_path):
        path = tmp_path / "db.sqlite"
        LegalDatabase(path).close()
        with LegalDatabase(path) as second:
            assert second.schema_version() >= 1

    def test_foreign_keys_cascade(self, database):
        database.save_document(make_document())
        database.delete_document("loi_2026_001")
        assert database.get_articles("loi_2026_001") == []
        assert database.get_pages("loi_2026_001") == []


class TestSaveAndRead:
    def test_document_is_saved_with_all_children(self, database):
        database.save_document(make_document(articles=3))
        row = database.get_document_row("loi_2026_001")
        assert row["title"] == "Loi portant organisation du travail"
        assert row["type"] == "loi"
        assert row["number"] == "2026-001"
        assert len(database.get_articles("loi_2026_001")) == 3
        assert len(database.get_pages("loi_2026_001")) == 2
        assert len(database.get_structure("loi_2026_001")) == 1
        assert len(database.get_relations("loi_2026_001")) == 1

    def test_pages_keep_raw_and_cleaned_text(self, database):
        """Le nettoyage doit rester contestable : les deux versions sont gardées."""
        database.save_document(make_document())
        page = database.get_page("loi_2026_001", 1)
        assert page["text"] == "Texte nettoye de la page 1"
        assert "BRUT" in page["raw_text"]

    def test_alineas_keep_their_order(self, database):
        document = make_document(articles=1)
        document.articles[0].alineas = [
            Alinea(index=0, text="Premier alinea", number="1°"),
            Alinea(index=1, text="Deuxieme alinea", number="2°"),
            Alinea(index=2, text="Troisieme alinea", number="3°"),
        ]
        database.save_document(document)
        alineas = database.get_alineas("loi_2026_001_article_1")
        assert [a["idx"] for a in alineas] == [0, 1, 2]
        assert alineas[1]["text"] == "Deuxieme alinea"

    def test_metadata_confidence_and_evidence_survive(self, database):
        database.save_document(make_document())
        row = database.get_document_row("loi_2026_001")
        metadata = rebuild_metadata(row)
        assert metadata.confidence["number"] == 0.92
        assert "2026-001" in metadata.evidence["number"]
        assert metadata.warnings

    def test_rewrite_is_idempotent(self, database):
        database.save_document(make_document(articles=3))
        database.save_document(make_document(articles=2))
        assert len(database.get_articles("loi_2026_001")) == 2, "pas d'accumulation"
        assert database.stats()["counts"]["documents"] == 1

    def test_human_validation_survives_reprocessing(self, database):
        """§16 : retraiter un document ne doit pas effacer le travail du relecteur."""
        database.save_document(make_document())
        database.set_validation("loi_2026_001", ValidationStatus.VALIDATED, "verifie le 30/08")
        database.save_document(make_document())
        row = database.get_document_row("loi_2026_001")
        assert row["validation"] == "valide"
        assert row["validation_note"] == "verifie le 30/08"

    def test_pending_validation_is_not_sticky(self, database):
        database.save_document(make_document())
        assert database.get_document_row("loi_2026_001")["validation"] == "en_attente"

    def test_failed_document_does_not_block_the_others(self, database, monkeypatch):
        """§26 : un document en erreur n'interrompt pas l'enregistrement."""
        good_one, bad, good_two = make_document("a"), make_document("b"), make_document("c")
        original = LegalDatabase.save_document

        def flaky(self, document, include_pages=True):
            if document.document_id == "b":
                raise sqlite3.OperationalError("panne simulée")
            return original(self, document, include_pages)

        monkeypatch.setattr(LegalDatabase, "save_document", flaky)
        assert database.save_documents([good_one, bad, good_two]) == 2
        assert {row["document_id"] for row in database.list_documents()} == {"a", "c"}


class TestTraceability:
    def test_article_traces_back_to_its_source(self, database):
        """§33 : article → document → page → fichier d'origine."""
        database.save_document(make_document())
        trace = database.trace_article("loi_2026_001_article_1")
        assert trace["article"]["article_number"] == "1"
        assert trace["document"]["filename"] == "loi_2026_001.pdf"
        assert trace["page"]["page"] == 1
        assert "Texte nettoye de la page 1" in trace["page"]["text"]
        assert trace["source_path"].endswith("loi_2026_001.pdf")
        assert trace["alineas"]

    def test_unknown_article_returns_none(self, database):
        assert database.trace_article("inexistant") is None

    def test_lookup_by_file_hash(self, database):
        database.save_document(make_document())
        assert database.find_by_file_hash("f" * 64)[0]["document_id"] == "loi_2026_001"

    def test_full_text_search(self, database):
        database.save_document(make_document(articles=3))
        results = database.search_articles("article 2")
        assert any(r["article_id"].endswith("_2") for r in results)


class TestStats:
    def test_counts_and_averages(self, database):
        database.save_document(make_document("a"))
        database.save_document(make_document("b"))
        stats = database.stats()
        assert stats["counts"]["documents"] == 2
        assert stats["counts"]["articles"] == 4
        assert stats["by_type"]["loi"] == 2
        assert stats["average_quality_score"] == pytest.approx(0.94)

    def test_empty_database(self, database):
        stats = database.stats()
        assert stats["counts"]["documents"] == 0
        assert stats["average_quality_score"] is None


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


class TestJsonlExport:
    def test_documents_jsonl(self, tmp_path):
        path = tmp_path / "documents.jsonl"
        assert export_documents_jsonl([make_document("a"), make_document("b")], path) == 2
        records = list(load_documents_jsonl(path))
        assert {r["document_id"] for r in records} == {"a", "b"}
        assert records[0]["type"] == "loi"

    def test_articles_jsonl_carries_document_context(self, tmp_path):
        """Un moteur RAG doit pouvoir citer sa source sans jointure."""
        path = tmp_path / "articles.jsonl"
        assert export_articles_jsonl([make_document(articles=3)], path) == 3
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert record["document_title"] == "Loi portant organisation du travail"
        assert record["document_number"] == "2026-001"
        assert record["document_date"] == "2026-02-10"
        assert record["source_file"] == "loi_2026_001.pdf"
        assert record["page_start"] == 1
        assert record["hierarchy_path"] == ["TITRE I"]

    def test_article_record_keeps_alineas_in_order(self):
        document = make_document(articles=1)
        document.articles[0].alineas = [
            Alinea(index=0, text="Premier"),
            Alinea(index=1, text="Second"),
        ]
        record = article_record(document, document.articles[0])
        assert [a["index"] for a in record["alineas"]] == [0, 1]

    def test_page_text_can_be_excluded(self, tmp_path):
        path = tmp_path / "documents.jsonl"
        export_documents_jsonl([make_document()], path, include_pages=False)
        assert "pages" not in json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    def test_jsonl_is_utf8_and_unescaped(self, tmp_path):
        path = tmp_path / "documents.jsonl"
        export_documents_jsonl([make_document()], path)
        assert "Assemblée nationale" in path.read_text(encoding="utf-8")

    def test_empty_corpus_produces_empty_file(self, tmp_path):
        path = tmp_path / "documents.jsonl"
        assert export_documents_jsonl([], path) == 0
        assert path.exists() and path.read_text(encoding="utf-8") == ""


class TestJsonExports:
    def test_metadata_json(self, tmp_path):
        path = tmp_path / "metadata.json"
        export_metadata_json([make_document("a"), make_document("b")], path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["document_count"] == 2
        assert payload["documents"][0]["confidence"]["date"] == 0.95

    def test_quality_report_json(self, tmp_path):
        path = tmp_path / "quality_report.json"
        document = make_document("c")
        document.quality.status = QualityStatus.REVIEW_REQUIRED
        document.quality.score = 0.6
        export_quality_report_json([make_document("a"), document], path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["reported_count"] == 2
        assert payload["documents_requiring_review"] == ["c"]
        assert payload["min_score"] == pytest.approx(0.6)

    def test_quality_report_on_empty_corpus(self, tmp_path):
        path = tmp_path / "quality_report.json"
        export_quality_report_json([], path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["average_score"] is None


class TestCsvExport:
    def test_articles_csv(self, tmp_path):
        path = tmp_path / "articles.csv"
        assert export_articles_csv([make_document(articles=2)], path) == 2
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("article_id,document_id,article_number")
        assert len(lines) == 3


class TestExportAll:
    def test_produces_every_configured_format(self, config):
        produced = export_all([make_document("a"), make_document("b")], config)
        assert {"documents_jsonl", "articles_jsonl", "metadata_json",
                "quality_report_json", "sqlite"} <= set(produced)
        for path in produced.values():
            from pathlib import Path

            assert Path(path).exists(), path

    def test_sqlite_export_is_queryable(self, config):
        export_all([make_document("a")], config)
        with LegalDatabase(config.path("database"), create=False) as database:
            assert database.stats()["counts"]["documents"] == 1
            assert database.trace_article("a_article_1")["document"]["filename"] == "a.pdf"

    def test_format_selection_is_respected(self, config):
        cfg = config.with_overrides({"export": {"formats": ["jsonl"]}})
        produced = export_all([make_document("a")], cfg)
        assert "sqlite" not in produced and "metadata_json" not in produced

    def test_parquet_is_reported_as_out_of_scope(self, config, caplog):
        cfg = config.with_overrides({"export": {"formats": ["parquet"]}})
        with caplog.at_level("WARNING"):
            produced = export_all([make_document("a")], cfg)
        assert "parquet" not in produced
        assert any("Parquet" in record.message for record in caplog.records)
