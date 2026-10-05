"""Le balisage d'un lecteur vision-langage ramené à du texte.

Cas réels de la tranche T001 du lot 1 (5 octobre 2026), lue par PaddleOCR-VL :

* décret 2022-324 : « DU $ 1^{er} $ JUIN 2022 » — le pipeline ne voyait pas la
  date de l'intitulé et retenait celle d'un visa (2017-11-15) ;
* accord 2020-378 : « $ \\underline{\\text{Article 2}} $ : » — l'article 2 n'était
  pas détecté ;
* tableaux rendus en HTML, gras en Markdown.
"""

from __future__ import annotations

import pytest

from bldp.core.cleaning.normalizer import CleaningReport, clean_page_text, strip_reader_markup
from bldp.core.metadata.engine import detect_date
from bldp.jurisdictions.registry import get_jurisdiction


class TestLatex:
    def test_exposant_dans_une_date(self):
        texte, n = strip_reader_markup("DÉCRET n° 2022-324 DU $ 1^{er} $ JUIN 2022 portant création")
        assert texte == "DÉCRET n° 2022-324 DU 1er JUIN 2022 portant création"
        assert n == 1

    def test_exposant_isole_se_colle_au_chiffre(self):
        texte, _ = strip_reader_markup("Article 1 $ ^{er} $ : sont nommées à titre normal")
        assert texte == "Article 1er : sont nommées à titre normal"

    def test_article_souligne(self):
        texte, _ = strip_reader_markup("da, (GNSP)\n$ \\underline{\\text{Article 2}} $ : Le présent décret sera")
        assert texte == "da, (GNSP)\nArticle 2 : Le présent décret sera"

    def test_commande_inconnue_garde_son_nom(self):
        texte, _ = strip_reader_markup("un écart de $ 3\\Delta t $ et $ \\frobnicate{x} $")
        assert "Δ" in texte and "frobnicate" in texte and "$" not in texte

    def test_des_dollars_sans_latex_restent(self):
        texte = "un prix de 5 $ et de 6 $ par unité"
        assert strip_reader_markup(texte) == (texte, 0)


class TestHtmlEtMarkdown:
    def test_tableau_en_lignes(self):
        texte, n = strip_reader_markup(
            "Répartition :\n<table><tr><td>Ministère</td><td>Montant</td></tr>"
            "<tr><td rowspan=\"2\">Santé</td><td>500 000 FCFA</td></tr></table>\nFin"
        )
        assert "Ministère | Montant" in texte
        assert "Santé | 500 000 FCFA" in texte
        assert "<" not in texte
        assert n >= 1

    def test_exposant_html(self):
        assert strip_reader_markup("le 1<sup>er</sup> juin 2022")[0] == "le 1er juin 2022"

    def test_gras_et_titres_markdown(self):
        texte, _ = strip_reader_markup("## TITRE I\n**ARTICLE 3** : Le présent arrêté")
        assert texte == "TITRE I\nARTICLE 3 : Le présent arrêté"

    def test_texte_sain_inchange(self):
        texte = "Article 4 : Le Ministre est chargé de l'exécution (voir n° 12 < 15 ; 3 * 4).\n#hashtag"
        assert strip_reader_markup(texte) == (texte, 0)


class TestDansLePipeline:
    def test_le_nettoyage_retire_le_balisage_et_le_compte(self, config):
        rapport = CleaningReport()
        texte = clean_page_text("ARRÊTÉ\n$ \\underline{\\text{Article 2}} $ : Le présent arrêté", config,
                                is_ocr=True, report=rapport)
        assert "Article 2 :" in texte and "$" not in texte
        assert rapport.reader_markup_removed == 1

    def test_la_date_de_l_intitule_redevient_lisible(self, config):
        benin = get_jurisdiction("benin")
        entete = clean_page_text(
            "DÉCRET n° 2022-324 DU $ 1^{er} $ JUIN 2022 portant création de l'Agence\n"
            "Vu le décret n° 2017-555 du 15 novembre 2017 portant attributions", config, is_ocr=True)
        assert detect_date(entete, benin, "2022-324")[0] == "2022-06-01"
