"""Tests de l'extraction native PyMuPDF (§8) et du chargement (§6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bldp.core.extraction.pymupdf_extractor import (
    ExtractionError,
    extract_document,
    extract_pdf_metadata,
    extract_toc,
    iter_page_texts,
)
from bldp.core.loader import (
    LoaderError,
    build_source_file,
    detect_category,
    discover_files,
    ingest,
)
from bldp.models import ExtractionMethod


class TestDiscovery:
    def test_finds_pdfs_recursively(self, tmp_path, make_text_pdf):
        (tmp_path / "lois").mkdir()
        make_text_pdf("a.pdf", ["Article 1er"])
        target = tmp_path / "lois" / "b.pdf"
        target.write_bytes((tmp_path / "a.pdf").read_bytes())
        (tmp_path / "note.txt").write_text("ignore", encoding="utf-8")

        found = discover_files(tmp_path, [".pdf"])
        assert {p.name for p in found} == {"a.pdf", "b.pdf"}

    def test_non_recursive(self, tmp_path, make_text_pdf):
        (tmp_path / "lois").mkdir()
        make_text_pdf("a.pdf", ["x"])
        (tmp_path / "lois" / "b.pdf").write_bytes((tmp_path / "a.pdf").read_bytes())
        found = discover_files(tmp_path, [".pdf"], recursive=False)
        assert {p.name for p in found} == {"a.pdf"}

    def test_single_file_accepted(self, text_pdf):
        assert discover_files(text_pdf, [".pdf"]) == [text_pdf]

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(LoaderError):
            discover_files(tmp_path / "absent", [".pdf"])

    def test_gitkeep_is_ignored(self, tmp_path):
        (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
        assert discover_files(tmp_path, [".pdf", ".gitkeep"]) == []

    def test_results_are_sorted(self, tmp_path, make_text_pdf):
        for name in ("c.pdf", "a.pdf", "b.pdf"):
            make_text_pdf(name, ["x"])
        found = discover_files(tmp_path, [".pdf"])
        assert [p.name for p in found] == ["a.pdf", "b.pdf", "c.pdf"]


class TestCategories:
    @pytest.mark.parametrize(
        "relative, expected",
        [
            ("lois/a.pdf", "lois"),
            ("codes/sous/b.pdf", "codes"),
            ("jurisprudence/c.pdf", "jurisprudence"),
            ("d.pdf", "autres"),
            ("dossier_libre/e.pdf", "autres"),
        ],
    )
    def test_detect_category(self, tmp_path, relative, expected):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4")
        assert detect_category(path, tmp_path) == expected


class TestIngest:
    def test_inventory_fields(self, tmp_path, text_pdf):
        source = build_source_file(text_pdf, tmp_path)
        assert source.document_id == "loi_2026_001"
        assert source.filename == "loi_2026_001.pdf"
        assert source.extension == ".pdf"
        assert source.size_bytes > 0
        assert len(source.file_hash) == 64
        assert source.ingested_at.endswith("+00:00")

    def test_ingest_copies_to_raw_without_touching_original(self, config, text_pdf):
        original_bytes = text_pdf.read_bytes()
        sources = ingest(text_pdf.parent, config)
        assert len(sources) == 1
        raw = Path(sources[0].raw_path)
        assert raw.exists()
        assert raw.parent == config.path("raw")
        assert raw.read_bytes() == original_bytes
        assert text_pdf.read_bytes() == original_bytes, "l'original ne doit jamais changer"

    def test_ingest_can_skip_copy(self, config, text_pdf):
        sources = ingest(text_pdf.parent, config, copy=False)
        assert sources[0].raw_path is None

    def test_duplicate_filenames_get_distinct_ids(self, config, tmp_path, make_text_pdf):
        make_text_pdf("doublon.pdf", ["Contenu A"])
        (tmp_path / "sous").mkdir()
        other = tmp_path / "sous" / "doublon.pdf"
        other.write_bytes((tmp_path / "doublon.pdf").read_bytes() + b"\n% variante")
        ids = [s.document_id for s in ingest(tmp_path, config)]
        assert len(set(ids)) == 2


class TestNativeExtraction:
    def test_extracts_every_page(self, text_pdf, legal_pages):
        result = extract_document(text_pdf, "loi_2026_001")
        assert result.method is ExtractionMethod.NATIVE
        assert len(result.pages) == len(legal_pages)

    def test_page_numbers_start_at_one_and_are_ordered(self, text_pdf):
        result = extract_document(text_pdf, "loi_2026_001")
        assert [p.page for p in result.pages] == [1, 2]

    def test_provenance_is_preserved(self, text_pdf):
        """§8 : conserver page + fichier source pour retrouver l'origine."""
        result = extract_document(text_pdf, "loi_2026_001")
        for page in result.pages:
            assert page.document_id == "loi_2026_001"
            assert page.source_file == "loi_2026_001.pdf"
            assert page.page >= 1

    def test_text_content_is_faithful(self, text_pdf):
        result = extract_document(text_pdf, "loi_2026_001")
        assert "Article 1er" in result.pages[0].text
        assert "TITRE PREMIER" in result.pages[0].text
        assert "Article 3" in result.pages[1].text
        assert "Article 3" not in result.pages[0].text

    def test_raw_text_is_kept_for_audit(self, text_pdf):
        result = extract_document(text_pdf, "loi_2026_001")
        assert all(p.raw_text == p.text for p in result.pages)

    def test_char_count_is_computed(self, text_pdf):
        result = extract_document(text_pdf, "loi_2026_001")
        assert all(p.char_count == len(p.text) for p in result.pages)
        assert result.total_chars > 200

    def test_page_subset(self, text_pdf):
        result = extract_document(text_pdf, "loi_2026_001", pages=[2])
        assert [p.page for p in result.pages] == [2]

    def test_empty_pages_are_flagged_not_dropped(self, empty_pdf):
        result = extract_document(empty_pdf, "vide")
        assert len(result.pages) == 3, "les pages vides restent dans le résultat"
        assert all("page_sans_texte" in p.warnings for p in result.pages)
        assert result.warnings and "3 page(s) sans texte" in result.warnings[0]

    def test_scanned_pdf_yields_no_text(self, scanned_pdf):
        """Un PDF image n'a pas de couche texte : le module 2 devra ordonner l'OCR."""
        result = extract_document(scanned_pdf, "loi_scannee")
        assert result.total_chars == 0

    def test_missing_file_raises_clear_error(self, tmp_path):
        with pytest.raises(ExtractionError, match="introuvable"):
            extract_document(tmp_path / "absent.pdf", "absent")

    def test_corrupt_file_raises_clear_error(self, tmp_path):
        broken = tmp_path / "casse.pdf"
        broken.write_bytes(b"ceci n'est pas un PDF")
        with pytest.raises(ExtractionError):
            extract_document(broken, "casse")

    def test_iter_page_texts_is_lazy(self, text_pdf):
        pairs = list(iter_page_texts(text_pdf))
        assert [n for n, _ in pairs] == [1, 2]


class TestPdfMetadata:
    def test_metadata_keys_present(self, text_pdf):
        meta = extract_pdf_metadata(text_pdf)
        assert meta["page_count"] == 2
        assert set(meta).issuperset({"pdf_title", "pdf_author", "pdf_producer"})

    def test_toc_is_empty_when_absent(self, text_pdf):
        assert extract_toc(text_pdf) == []
