"""Tests du nettoyage (§9).

L'enjeu de ces tests est double : vérifier que les artefacts disparaissent, et
surtout **prouver que le contenu juridique survit**. La seconde moitié du
fichier est entièrement consacrée à ce qui ne doit jamais être supprimé.
"""

from __future__ import annotations

import pytest

from bldp.core.cleaning.normalizer import (
    apply_ocr_fixes,
    clean_page_text,
    clean_pages,
    collapse_whitespace,
    detect_repeated_lines,
    fix_hyphenation,
    is_page_number_line,
    is_protected,
    join_wrapped_lines,
    normalize_unicode,
    rejoin_split_article_headers,
    strip_control_chars,
)
from bldp.models import ExtractionMethod, Page


def make_pages(texts: list[str], method=ExtractionMethod.NATIVE) -> list[Page]:
    return [
        Page(document_id="doc", page=i + 1, text=t, source_file="doc.pdf", method=method)
        for i, t in enumerate(texts)
    ]


# ---------------------------------------------------------------------------
# Transformations élémentaires
# ---------------------------------------------------------------------------


class TestWhitespace:
    def test_multiple_spaces_collapse(self):
        assert collapse_whitespace("Le    contrat   de  travail") == "Le contrat de travail"

    def test_blank_lines_are_capped(self):
        assert collapse_whitespace("a\n\n\n\n\nb") == "a\n\nb"

    def test_trailing_spaces_removed(self):
        assert collapse_whitespace("  Article 1er  \n   texte  ") == "Article 1er\ntexte"


class TestControlChars:
    def test_removes_control_characters(self):
        text, removed = strip_control_chars("Article\x00 1er\x07")
        assert text == "Article 1er"
        assert removed == 2

    def test_keeps_newlines_and_tabs(self):
        text, _ = strip_control_chars("a\nb\tc")
        assert text == "a\nb\tc"


class TestUnicode:
    def test_nbsp_becomes_space(self):
        assert normalize_unicode("Article 1er") == "Article 1er"

    def test_zero_width_removed(self):
        assert normalize_unicode("Arti​cle") == "Article"

    def test_curly_quotes_normalised(self):
        assert normalize_unicode("l’employeur") == "l'employeur"

    def test_crlf_normalised(self):
        assert normalize_unicode("a\r\nb") == "a\nb"


class TestHyphenation:
    def test_joins_split_word(self):
        text, count = fix_hyphenation("la respon-\nsabilité civile")
        assert text == "la responsabilité civile"
        assert count == 1

    def test_keeps_meaningful_hyphen_before_digit(self):
        """« 2026-\\n001 » est un numéro de loi : le tiret est signifiant."""
        text, count = fix_hyphenation("loi 2026-\n001")
        assert "2026-" in text and count == 0

    def test_keeps_hyphen_before_capital(self):
        text, count = fix_hyphenation("Franco-\nBéninois")
        assert text == "Franco-\nBéninois" and count == 0


class TestWrappedLines:
    def test_joins_sentence_split_across_lines(self):
        text, count = join_wrapped_lines("Le contrat de travail est\nconclu librement.")
        assert text == "Le contrat de travail est conclu librement."
        assert count == 1

    def test_does_not_join_after_full_stop(self):
        text, _ = join_wrapped_lines("Premier alinéa.\nSecond alinéa.")
        assert text.count("\n") == 1

    def test_does_not_join_before_capital(self):
        """Une majuscule en début de ligne annonce probablement une unité neuve."""
        text, _ = join_wrapped_lines("dispositions générales\nArticle 2 : suite")
        assert "\nArticle 2" in text

    def test_does_not_join_bullet_lists(self):
        text, _ = join_wrapped_lines("les conditions suivantes\n- première condition")
        assert "\n- première" in text


class TestOcrFixes:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Artic1e 45", "Article 45"),
            ("ArticIe 45", "Article 45"),
            ("l'an 2O26", "l'an 2026"),
            ("n ° 12", "n° 12"),
            ("ﬁnancier", "financier"),
        ],
    )
    def test_safe_corrections(self, raw, expected):
        text, count = apply_ocr_fixes(raw)
        assert text == expected and count >= 1

    def test_does_not_touch_letters_outside_numbers(self):
        """Le « O » d'un mot ne doit jamais devenir un zéro."""
        text, _ = apply_ocr_fixes("OBLIGATION du travailleur")
        assert text == "OBLIGATION du travailleur"


class TestPageNumbers:
    @pytest.mark.parametrize(
        "line", ["12", " 3 ", "- 45 -", "Page 7", "[12]", "12 / 340", "(8)"]
    )
    def test_detected(self, line):
        assert is_page_number_line(line) is True

    @pytest.mark.parametrize(
        "line",
        [
            "Article 12",
            "12 mois de salaire",
            "Loi 2026-001",
            "",
            "Le contrat est conclu pour 12 mois",
        ],
    )
    def test_not_detected(self, line):
        assert is_page_number_line(line) is False


# ---------------------------------------------------------------------------
# En-têtes et pieds de page
# ---------------------------------------------------------------------------


class TestRepeatedLines:
    def test_detects_repeated_header(self):
        pages = [["JOURNAL OFFICIEL", "", "Contenu " + str(i)] for i in range(6)]
        assert detect_repeated_lines(pages, position="top")

    def test_ignores_varying_digits(self):
        """« page 1 », « page 2 »... sont le même en-tête."""
        pages = [[f"Bulletin officiel page {i}", "", "texte"] for i in range(6)]
        assert len(detect_repeated_lines(pages, position="top")) == 1

    def test_short_document_yields_nothing(self):
        """Sans répétition observable, on ne suppose rien."""
        pages = [["JOURNAL OFFICIEL", "texte"]] * 2
        assert detect_repeated_lines(pages, position="top") == set()

    def test_legal_lines_are_never_candidates(self):
        """Un article répété reste un article, jamais un en-tête à supprimer."""
        pages = [
            ["Article 1er : disposition", "En-tete neutre repete", "corps du texte", "fin"]
            for _ in range(8)
        ]
        repeated = detect_repeated_lines(pages, zone_lines=2, position="top")
        assert any("en tete neutre repete" in r for r in repeated)
        assert not any("article" in r for r in repeated)

    def test_zones_never_overlap_on_short_pages(self):
        """Sur une page courte, le corps ne doit pas tomber dans les deux zones."""
        pages = [["EN-TETE", f"Corps different numero {i}", "PIED"] for i in range(8)]
        top = detect_repeated_lines(pages, zone_lines=3, position="top")
        bottom = detect_repeated_lines(pages, zone_lines=3, position="bottom")
        assert top == {"en tete"} and bottom == {"pied"}

    def test_detects_repeated_footer(self):
        pages = [["texte", "", "Secretariat General du Gouvernement"] for _ in range(6)]
        assert detect_repeated_lines(pages, position="bottom")


class TestDocumentCleaning:
    def test_removes_headers_and_footers_across_pages(self, config):
        pages = make_pages(
            [
                f"BULLETIN OFFICIEL\n\nDisposition numero {i} du texte.\n\nImprimerie nationale"
                for i in range(1, 7)
            ]
        )
        cleaned, report = clean_pages(pages, config)
        assert all("BULLETIN OFFICIEL" not in p.text for p in cleaned)
        assert all("Imprimerie nationale" not in p.text for p in cleaned)
        assert report.removed_headers and report.removed_footers
        assert all("Disposition numero" in p.text for p in cleaned)

    def test_raw_text_is_preserved_for_audit(self, config):
        pages = make_pages(["BULLETIN OFFICIEL\n\ntexte  utile"] * 4)
        cleaned, _ = clean_pages(pages, config)
        assert all("BULLETIN OFFICIEL" in p.raw_text for p in cleaned)

    def test_page_numbers_and_provenance(self, config):
        pages = make_pages([f"En-tete constant\n\nContenu {i}\n\n{i}" for i in range(1, 8)])
        cleaned, report = clean_pages(pages, config)
        assert [p.page for p in cleaned] == list(range(1, 8))
        assert report.removed_page_numbers >= 5
        assert all(p.source_file == "doc.pdf" for p in cleaned)

    def test_report_counts_are_coherent(self, config):
        pages = make_pages(["En-tete\n\nDu   texte   utile\n\n1"] * 5)
        _, report = clean_pages(pages, config)
        assert report.pages_processed == 5
        assert report.chars_after < report.chars_before
        assert report.chars_removed == report.chars_before - report.chars_after
        assert 0 <= report.removal_ratio <= 1

    def test_massive_removal_is_flagged(self, config):
        """Une suppression massive doit alerter, pas passer inaperçue."""
        pages = make_pages(["En-tete repetitif constant\nPied repetitif constant"] * 8)
        _, report = clean_pages(pages, config)
        if report.removal_ratio > 0.25:
            assert any("vérification humaine" in w for w in report.warnings)

    def test_emptied_page_is_flagged(self, config):
        pages = make_pages(["En-tete repete\n1"] * 6)
        cleaned, report = clean_pages(pages, config)
        emptied = [p for p in cleaned if not p.text.strip()]
        if emptied:
            assert all("page_videe_par_le_nettoyage" in p.warnings for p in emptied)
            assert any("vidée" in w for w in report.warnings)

    def test_ocr_fixes_only_on_ocr_pages(self, config):
        native = make_pages(["Artic1e 45 : disposition."], ExtractionMethod.NATIVE)
        ocr = make_pages(["Artic1e 45 : disposition."], ExtractionMethod.OCR)
        assert "Artic1e" in clean_pages(native, config)[0][0].text
        assert "Article 45" in clean_pages(ocr, config)[0][0].text

    def test_empty_input(self, config):
        cleaned, report = clean_pages([], config)
        assert cleaned == [] and report.pages_processed == 0

    def test_cleaning_can_be_switched_off(self, config):
        cfg = config.with_overrides(
            {
                "cleaning": {
                    "remove_repeated_headers": False,
                    "remove_repeated_footers": False,
                    "remove_page_numbers": False,
                }
            }
        )
        pages = make_pages(["EN-TETE\n\ncontenu\n\n1"] * 6)
        cleaned, _ = clean_pages(pages, cfg)
        assert all("EN-TETE" in p.text for p in cleaned)


class TestOnRealPdf:
    def test_journal_officiel_headers_are_removed(self, headers_pdf, config):
        from bldp.core.extraction.pymupdf_extractor import extract_document

        result = extract_document(headers_pdf, "journal")
        cleaned, report = clean_pages(result.pages, config)
        joined = "\n".join(p.text for p in cleaned)
        assert "JOURNAL OFFICIEL" not in joined
        assert "Secretariat General" not in joined
        # Les quatre articles survivent intégralement.
        for number in ("1er", "2", "3", "4"):
            assert f"Article {number}" in joined
        assert report.removed_headers


# ---------------------------------------------------------------------------
# Ce qui ne doit JAMAIS être supprimé (§9)
# ---------------------------------------------------------------------------


class TestLegalContentIsNeverRemoved:
    @pytest.mark.parametrize(
        "line",
        [
            "Article 45 : dispositions particulieres",
            "Art. 12",
            "Article premier",
            "Le deuxieme alinea de l'article 3",
            "Loi n° 2026-001 du 10 fevrier 2026",
            "Decret n° 2020-113",
            "une amende de 500 000 francs CFA",
            "un emprisonnement de six mois",
            "sauf dispositions contraires",
            "Toutefois, le contrat peut etre rompu",
            "nonobstant toute clause contraire",
            "sous reserve des dispositions de l'article 4",
            "a peine de nullite",
            "le 10 fevrier 2026",
            "12/03/2026",
            "un taux de 5 %",
        ],
    )
    def test_protected(self, line):
        assert is_protected(line) is True, f"ligne juridique non protégée : {line!r}"

    @pytest.mark.parametrize(
        "line",
        ["JOURNAL OFFICIEL", "Imprimerie Nationale", "12", "-------", "www.sgg.gouv.bj"],
    )
    def test_not_protected(self, line):
        assert is_protected(line) is False

    def test_protected_header_survives_repetition(self, config):
        """Même répété sur toutes les pages, un article n'est pas un en-tête."""
        pages = make_pages(["Article 1er : disposition repetee\n\nsuite du texte"] * 8)
        cleaned, report = clean_pages(pages, config)
        assert all("Article 1er" in p.text for p in cleaned)
        assert report.protected_lines_kept >= 8

    def test_amounts_dates_and_numbers_survive(self, config):
        text = (
            "Article 12 : Est puni d'une amende de 500 000 francs CFA et d'un\n"
            "emprisonnement de 6 mois, tout employeur qui, au 10 fevrier 2026,\n"
            "n'a pas respecte les dispositions de la loi n° 2026-001."
        )
        cleaned = clean_page_text(text, config)
        for fragment in ("500 000 francs CFA", "6 mois", "10 fevrier 2026", "2026-001"):
            assert fragment in cleaned, f"contenu juridique perdu : {fragment}"

    def test_alinea_order_is_preserved(self, config):
        text = "Article 5 :\nPremier alinea du texte.\nDeuxieme alinea du texte.\nTroisieme alinea."
        cleaned = clean_page_text(text, config)
        assert cleaned.index("Premier") < cleaned.index("Deuxieme") < cleaned.index("Troisieme")

    def test_exceptions_and_conditions_survive(self, config):
        text = (
            "Article 8 : Le contrat prend fin a son terme.\n"
            "Toutefois, sauf clause contraire, il peut etre renouvele\n"
            "sous reserve de l'accord des parties, a peine de nullite."
        )
        cleaned = clean_page_text(text, config)
        for fragment in ("Toutefois", "sauf clause contraire", "sous reserve", "a peine de nullite"):
            assert fragment in cleaned

    def test_no_word_is_lost_on_plain_legal_text(self, config):
        """Sur un texte sans artefact, le nettoyage ne retire aucun mot."""
        text = (
            "Article 3 : Le contrat de travail est conclu librement. "
            "Il peut etre a duree determinee ou indeterminee."
        )
        cleaned = clean_page_text(text, config)
        assert set(text.split()) == set(cleaned.split())


class TestSplitArticleHeaders:
    """Régression trouvée sur des lois béninoises scannées.

    Tesseract éclate fréquemment l'en-tête sur plusieurs lignes ::

        Article
        88
        :

    Le parser, qui raisonne ligne par ligne, ne le reconnaissait pas et
    **l'article disparaissait du corpus sans avertissement** — 13 articles
    perdus sur le corpus de test.
    """

    def test_header_split_over_three_lines(self):
        texte, count = rejoin_split_article_headers(
            "Article\n88\n:\nLa cessation des fonctions d'un roi."
        )
        assert count == 1
        assert texte.splitlines()[0] == "Article 88:"

    def test_number_glued_to_the_body(self):
        texte, count = rejoin_split_article_headers(
            "Article\n18:Tout programme comporte un film."
        )
        assert count == 1
        assert texte.startswith("Article 18:Tout programme")

    @pytest.mark.parametrize(
        "texte",
        [
            "Article 12 : deja complet.",
            "Article 5\nLe contrat est conclu.",
            "Article\ndu present texte",
            "Article\nde la loi citee est abroge.",
        ],
    )
    def test_nothing_is_merged_without_a_number(self, texte):
        """Règle volontairement étroite : jamais de fusion spéculative."""
        assert rejoin_split_article_headers(texte) == (texte, 0)

    def test_at_most_three_lines_are_absorbed(self):
        """Un en-tête ne doit jamais avaler un paragraphe entier."""
        texte, _ = rejoin_split_article_headers("Article\n1\n2\n3\n4\n5\n6")
        assert len(texte.splitlines()[0]) < 20

    def test_the_split_article_is_then_parsed(self, config):
        """Vérification de bout en bout : l'article est bien extrait."""
        from bldp.core.parser.legal_parser import parse_document

        pages = make_pages(
            [
                "Article\n37\n:\nDisposition precedente du texte juridique.\n"
                "Article\n38\n:\nLa cessation des fonctions d'un roi intervient."
            ]
        )
        cleaned, report = clean_pages(pages, config)
        assert report.rejoined_article_headers == 2
        result = parse_document(cleaned, "doc", config)
        assert [a.article_number for a in result.articles] == ["37", "38"]

    def test_can_be_disabled(self, config):
        cfg = config.with_overrides({"cleaning": {"rejoin_article_headers": False}})
        cleaned, report = clean_pages(make_pages(["Article\n88\n:\nTexte."]), cfg)
        assert report.rejoined_article_headers == 0
