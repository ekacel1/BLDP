"""Tests du module 2 — analyse du PDF et décision d'OCR (§7)."""

from __future__ import annotations

import pytest

from bldp.core.classifier import (
    _alpha_ratio,
    _decide,
    analyze_or_none,
    analyze_pdf,
    decide_extraction_route,
)
from bldp.models import PageAnalysis


def make_pages(specs: list[tuple[int, int, float]]) -> list[PageAnalysis]:
    """Fabrique des mesures de page : ``(caractères, images, ratio_alpha)``."""
    return [
        PageAnalysis(
            page=index + 1,
            char_count=chars,
            image_count=images,
            has_text=chars > 0,
            alpha_ratio=alpha,
        )
        for index, (chars, images, alpha) in enumerate(specs)
    ]


def decide(pages, **kwargs):
    defaults = dict(
        document_id="doc",
        page_count=len(pages),
        size_bytes=1000,
        pages_detail=pages,
        encrypted=False,
        min_chars=100,
        min_ratio=0.60,
        min_alpha=0.55,
        sampled=False,
    )
    defaults.update(kwargs)
    return _decide(**defaults)


class TestOnRealPdfs:
    def test_native_pdf_does_not_need_ocr(self, text_pdf, config):
        analysis = analyze_pdf(text_pdf, "loi", config)
        assert analysis.pages == 2
        assert analysis.has_text is True
        assert analysis.ocr_required is False
        assert analysis.confidence > 0.8
        assert analysis.total_chars > 200

    def test_scanned_pdf_needs_ocr(self, scanned_pdf, config):
        analysis = analyze_pdf(scanned_pdf, "scan", config)
        assert analysis.has_text is False
        assert analysis.ocr_required is True
        assert analysis.total_images >= 1
        assert analysis.confidence > 0.9
        assert any("scanné" in r for r in analysis.reasons)

    def test_empty_pdf_is_not_sent_to_ocr_blindly(self, empty_pdf, config):
        """Un PDF vide sans image n'est pas un scan : il est signalé tel quel."""
        analysis = analyze_pdf(empty_pdf, "vide", config)
        assert analysis.pages == 3
        assert analysis.has_text is False
        assert analysis.total_images == 0

    def test_analysis_reports_page_geometry(self, text_pdf, config):
        analysis = analyze_pdf(text_pdf, "loi", config)
        assert analysis.pages_detail[0].width == pytest.approx(595, abs=1)
        assert analysis.pages_detail[0].height == pytest.approx(842, abs=1)

    def test_expected_json_shape(self, text_pdf, config):
        """Le §7 impose pages / has_text / ocr_required / confidence."""
        payload = analyze_pdf(text_pdf, "loi", config).to_dict()
        assert set(payload).issuperset({"pages", "has_text", "ocr_required", "confidence"})
        assert isinstance(payload["confidence"], float)

    def test_unreadable_pdf_returns_none_instead_of_raising(self, tmp_path, config):
        broken = tmp_path / "casse.pdf"
        broken.write_bytes(b"pas un pdf")
        assert analyze_or_none(broken, "casse", config) is None


class TestDecisionRules:
    def test_all_pages_rich_means_native(self):
        analysis = decide(make_pages([(1200, 0, 0.8)] * 5))
        assert analysis.ocr_required is False
        assert analysis.text_page_ratio == 1.0
        assert analysis.confidence >= 0.95

    def test_no_text_means_ocr(self):
        analysis = decide(make_pages([(0, 1, 0.0)] * 4))
        assert analysis.ocr_required is True
        assert "aucun texte natif" in analysis.reasons[0]

    def test_mostly_empty_pages_means_ocr(self):
        # 1 page riche sur 5 -> ratio 0.2 < 0.60
        pages = make_pages([(1200, 0, 0.8)] + [(5, 1, 0.5)] * 4)
        analysis = decide(pages)
        assert analysis.ocr_required is True
        assert any("atteignent" in r for r in analysis.reasons)

    def test_garbled_text_triggers_ocr(self):
        """Texte abondant mais non alphabétique : police cassée ou OCR dégradé."""
        pages = make_pages([(2000, 0, 0.20)] * 3)
        analysis = decide(pages)
        assert analysis.ocr_required is True
        assert any("suspect" in r for r in analysis.reasons)

    def test_few_weak_pages_are_reported_for_targeted_ocr(self):
        pages = make_pages([(1200, 0, 0.8)] * 8 + [(10, 1, 0.4)] * 2)
        analysis = decide(pages)
        assert analysis.ocr_required is False
        assert analysis.pages_needing_ocr == [9, 10]

    def test_empty_document(self):
        analysis = decide([], page_count=0)
        assert analysis.ocr_required is False
        assert analysis.confidence == 0.0
        assert "vide" in analysis.reasons[0]

    def test_decision_is_always_motivated(self):
        for pages in (
            make_pages([(1200, 0, 0.8)] * 3),
            make_pages([(0, 2, 0.0)] * 3),
            make_pages([(2000, 0, 0.1)] * 3),
        ):
            assert decide(pages).reasons, "toute décision doit être justifiée"

    def test_thresholds_come_from_config(self, config):
        """Aucun seuil codé en dur : ils viennent de la section `classifier`."""
        pages = make_pages([(150, 0, 0.8)] * 4)
        assert decide(pages, min_chars=100).ocr_required is False
        assert decide(pages, min_chars=500).ocr_required is True


class TestConfidence:
    def test_borderline_case_has_low_confidence(self):
        """Juste au-dessus du seuil : le pipeline doit douter, pas trancher."""
        pages = make_pages([(1200, 0, 0.8)] * 3 + [(5, 0, 0.0)] * 2)  # ratio 0.60
        analysis = decide(pages)
        assert analysis.confidence < 0.70
        assert any("confiance faible" in r for r in analysis.reasons)

    def test_clear_case_has_high_confidence(self):
        analysis = decide(make_pages([(3000, 0, 0.82)] * 10))
        assert analysis.confidence >= 0.95

    def test_confidence_stays_within_bounds(self):
        for pages in (
            make_pages([(0, 0, 0.0)]),
            make_pages([(5000, 0, 0.9)] * 20),
            make_pages([(120, 0, 0.56)] * 3),
        ):
            assert 0.0 <= decide(pages).confidence <= 1.0

    def test_sampling_lowers_confidence(self):
        pages = make_pages([(3000, 0, 0.82)] * 10)
        full = decide(pages)
        partial = decide(pages, page_count=500, sampled=True)
        assert partial.confidence < full.confidence
        assert any("échantillon" in r for r in partial.reasons)


class TestRouting:
    def test_native_route(self, config):
        analysis = decide(make_pages([(1200, 0, 0.8)] * 3))
        assert decide_extraction_route(analysis, config) == "native"

    def test_ocr_route(self, config):
        analysis = decide(make_pages([(0, 1, 0.0)] * 3))
        assert decide_extraction_route(analysis, config) == "ocr"

    def test_hybrid_route(self, config):
        analysis = decide(make_pages([(1200, 0, 0.8)] * 8 + [(3, 1, 0.2)] * 2))
        assert decide_extraction_route(analysis, config) == "hybrid"

    def test_disabled_ocr_falls_back_to_native(self, config):
        """OCR coupé : on extrait ce qui existe et on le signale, sans échouer."""
        analysis = decide(make_pages([(0, 1, 0.0)] * 3))
        cfg = config.with_overrides({"ocr": {"enabled": False}})
        assert decide_extraction_route(analysis, cfg) == "native"


class TestAlphaRatio:
    def test_clean_french_text(self):
        assert _alpha_ratio("Le contrat de travail est conclu librement.") > 0.85

    def test_symbol_soup(self):
        assert _alpha_ratio("#@$%^&*()_+={}[]|\\<>~`") < 0.1

    def test_empty(self):
        assert _alpha_ratio("   \n\t ") == 0.0
