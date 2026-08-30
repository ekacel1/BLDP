"""Tests du parser juridique (§10, §11 et §24)."""

from __future__ import annotations

import pytest

from bldp.core.parser.legal_parser import (
    check_numbering,
    detect_headings,
    linearize,
    parse_document,
    split_alineas,
)
from bldp.core.parser.rules import generic_ruleset
from bldp.jurisdictions.registry import (
    JurisdictionError,
    available_jurisdictions,
    get_jurisdiction,
    get_ruleset,
)
from bldp.models import Page, StructureLevel


def make_pages(texts: list[str]) -> list[Page]:
    return [
        Page(document_id="doc", page=i + 1, text=t, source_file="doc.pdf")
        for i, t in enumerate(texts)
    ]


def parse(texts: list[str], config, jurisdiction: str | None = None):
    ruleset = (
        get_jurisdiction(jurisdiction).ruleset if jurisdiction else generic_ruleset()
    )
    return parse_document(make_pages(texts), "doc", config, ruleset=ruleset)


# ---------------------------------------------------------------------------
# Détection des articles (§24 : les quatre formes imposées)
# ---------------------------------------------------------------------------


class TestArticleForms:
    @pytest.mark.parametrize(
        "header, expected_number",
        [
            ("Article 1", "1"),
            ("Article 1er", "1er"),
            ("Article premier", "premier"),
            ("Art. 1", "1"),
            ("ARTICLE 1er", "1er"),
            ("Article 45", "45"),
            ("Article 45 bis", "45 bis"),
            ("Art 12", "12"),
            ("Article 45-2", "45-2"),
        ],
    )
    def test_recognised_forms(self, header, expected_number, config):
        result = parse([f"{header} : Le contrat de travail est conclu librement."], config)
        assert result.article_count == 1, f"forme non reconnue : {header!r}"
        assert result.articles[0].article_number == expected_number

    def test_all_four_mandatory_forms_in_one_document(self, config):
        """§24 : Article 1 / Article 1er / Article premier / Art. 1."""
        result = parse(
            [
                "Article 1 : Premiere disposition du texte juridique.\n"
                "Article 2 : Deuxieme disposition du texte juridique.\n"
                "Art. 3 : Troisieme disposition du texte juridique.\n"
                "Article 4 : Quatrieme disposition du texte juridique."
            ],
            config,
        )
        assert [a.article_number for a in result.articles] == ["1", "2", "3", "4"]

    def test_article_number_is_normalised(self, config):
        result = parse(["Article 1 er : disposition du texte."], config)
        assert result.articles[0].article_number == "1er"

    def test_word_in_sentence_is_not_an_article(self, config):
        """« l'article 5 dispose que » au fil du texte ne crée pas d'article."""
        result = parse(
            ["Article 1er : Le present texte renvoie a l'article 5 pour les modalites."],
            config,
        )
        assert result.article_count == 1

    def test_text_without_articles_is_reported_not_invented(self, config):
        result = parse(["Rapport annuel d'activite. Presentation des travaux."], config)
        assert result.article_count == 0
        assert any("aucun article" in w for w in result.warnings)
        assert result.preamble, "le texte n'est pas perdu pour autant"


class TestArticleContent:
    def test_text_runs_until_next_article(self, config):
        result = parse(
            [
                "Article 1er : Premiere disposition.\n"
                "Suite de la premiere disposition.\n"
                "Article 2 : Seconde disposition."
            ],
            config,
        )
        assert "Suite de la premiere" in result.articles[0].text
        assert "Seconde disposition" not in result.articles[0].text

    def test_inline_text_after_number_is_content_not_heading(self, config):
        result = parse(["Article 45 : Le salarie a droit a un repos hebdomadaire."], config)
        article = result.articles[0]
        assert article.text.startswith("Le salarie a droit")
        assert article.label == "Article 45 : Le salarie a droit a un repos hebdomadaire."

    def test_last_article_runs_to_end_of_document(self, config):
        result = parse(
            ["Article 1er : Premiere.", "Article 2 : Derniere disposition du texte.\nSuite finale."],
            config,
        )
        assert "Suite finale" in result.articles[-1].text

    def test_short_article_is_flagged(self, config):
        result = parse(["Article 1er : Abroge.", "Article 2 : Disposition suffisamment longue."], config)
        assert "article_potentiellement_incomplet" in result.articles[0].warnings

    def test_preamble_is_kept(self, config):
        result = parse(
            [
                "REPUBLIQUE DU BENIN\nLOI N 2026-001\n"
                "L'Assemblee nationale a delibere et adopte.\n"
                "Article 1er : Premiere disposition du texte."
            ],
            config,
        )
        assert "REPUBLIQUE DU BENIN" in result.preamble
        assert "Article 1er" not in result.preamble


# ---------------------------------------------------------------------------
# Hiérarchie (§10)
# ---------------------------------------------------------------------------


DOCUMENT_HIERARCHIQUE = [
    "TITRE II\nDES RELATIONS DE TRAVAIL\n"
    "CHAPITRE III\nDU CONTRAT\n"
    "Section 2\nDe la formation\n"
    "Article 45 : Le contrat est conclu librement entre les parties."
]


class TestHierarchy:
    def test_levels_are_detected(self, config):
        result = parse(DOCUMENT_HIERARCHIQUE, config)
        levels = [node.level for node in result.structure]
        assert levels == [
            StructureLevel.TITRE,
            StructureLevel.CHAPITRE,
            StructureLevel.SECTION,
        ]

    def test_article_carries_full_context(self, config):
        """§11 : chaque article conserve son contexte hiérarchique."""
        article = parse(DOCUMENT_HIERARCHIQUE, config).articles[0]
        assert article.title == "TITRE II"
        assert article.chapter == "CHAPITRE III"
        assert article.section == "Section 2"
        assert article.hierarchy_path == ["TITRE II", "CHAPITRE III", "Section 2"]

    def test_nesting_is_correct(self, config):
        result = parse(DOCUMENT_HIERARCHIQUE, config)
        titre, chapitre, section = result.structure
        assert titre.parent_id is None
        assert chapitre.parent_id == titre.node_id
        assert section.parent_id == chapitre.node_id
        assert [titre.depth, chapitre.depth, section.depth] == [0, 1, 2]

    def test_new_chapter_closes_previous_section(self, config):
        result = parse(
            [
                "CHAPITRE I\nSection 1\nArticle 1er : Premiere disposition du texte.\n"
                "CHAPITRE II\nArticle 2 : Seconde disposition du texte."
            ],
            config,
        )
        first, second = result.articles
        assert first.section == "Section 1"
        assert second.section is None, "la section du chapitre précédent ne doit pas fuir"
        assert second.chapter == "CHAPITRE II"

    def test_all_levels_of_the_spec_are_supported(self, config):
        text = (
            "PREMIERE PARTIE\nLIVRE I\nTITRE II\nSOUS-TITRE 1\nCHAPITRE III\n"
            "Section 2\nSous-section 3\nParagraphe 4\n"
            "Article 45 : Disposition finale du texte juridique.\n"
            "ANNEXE I\nArticle 1er : Disposition annexe du texte."
        )
        result = parse([text], config)
        detected = {node.level for node in result.structure}
        assert detected >= {
            StructureLevel.PARTIE,
            StructureLevel.LIVRE,
            StructureLevel.TITRE,
            StructureLevel.SOUS_TITRE,
            StructureLevel.CHAPITRE,
            StructureLevel.SECTION,
            StructureLevel.SOUS_SECTION,
            StructureLevel.PARAGRAPHE,
            StructureLevel.ANNEXE,
        }

    def test_sous_section_wins_over_section(self, config):
        result = parse(["Sous-section 3\nArticle 1er : disposition du texte."], config)
        assert result.structure[0].level is StructureLevel.SOUS_SECTION

    def test_unnumbered_subdivision_is_not_invented(self, config):
        """« la section du contrat » dans une phrase n'est pas une subdivision."""
        result = parse(
            ["Article 1er : Les regles de la section applicable sont precisees."], config
        )
        assert result.structure == []


# ---------------------------------------------------------------------------
# Provenance (§33)
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_article_knows_its_pages(self, config):
        result = parse(
            [
                "Article 1er : Premiere disposition du texte juridique.",
                "Suite de la premiere disposition sur la page deux.\n"
                "Article 2 : Seconde disposition du texte.",
            ],
            config,
        )
        first, second = result.articles
        assert first.page_start == 1
        assert first.page_end == 2, "l'article déborde sur la page suivante"
        assert second.page_start == 2

    def test_char_offsets_point_into_the_full_text(self, config):
        pages = make_pages(["Article 1er : Une disposition.\nArticle 2 : Une autre disposition."])
        result = parse_document(pages, "doc", config)
        _, full_text = linearize(pages)
        for article in result.articles:
            assert full_text[article.char_start:].startswith(article.label[:20])

    def test_source_file_is_propagated(self, config):
        result = parse(["Article 1er : Une disposition du texte."], config)
        assert result.articles[0].source_file == "doc.pdf"

    def test_article_ids_are_stable_and_unique(self, config):
        result = parse(
            ["Article 1er : Premiere.\nArticle 2 : Seconde disposition du texte."], config
        )
        ids = [a.article_id for a in result.articles]
        assert ids == ["doc_article_1er", "doc_article_2"]
        assert len(set(ids)) == len(ids)

    def test_duplicate_numbers_are_disambiguated_and_flagged(self, config):
        result = parse(
            [
                "Article 1er : Disposition du corps du texte.\n"
                "ANNEXE I\n"
                "Article 1er : Disposition de l'annexe du texte."
            ],
            config,
        )
        ids = [a.article_id for a in result.articles]
        assert len(set(ids)) == 2
        assert "numero_article_duplique_dans_le_document" in result.articles[1].warnings


# ---------------------------------------------------------------------------
# Alinéas (§11)
# ---------------------------------------------------------------------------


class TestAlineas:
    def test_numbered_alineas_keep_their_order(self, config):
        result = parse(
            [
                "Article 5 : Sont consideres comme travailleurs :\n"
                "1° les salaries du secteur prive ;\n"
                "2° les agents contractuels de l'Etat ;\n"
                "3° les apprentis."
            ],
            config,
        )
        alineas = result.articles[0].alineas
        assert len(alineas) == 4  # le chapeau + les trois énumérations
        assert [a.index for a in alineas] == [0, 1, 2, 3]
        assert "1°" in alineas[1].number
        assert "salaries" in alineas[1].text

    def test_letter_alineas(self):
        alineas = split_alineas(
            "a) premiere condition\nb) seconde condition", generic_ruleset().alinea_pattern
        )
        assert len(alineas) == 2

    def test_dash_alineas(self):
        alineas = split_alineas(
            "- premiere condition\n- seconde condition", generic_ruleset().alinea_pattern
        )
        assert len(alineas) == 2

    def test_paragraph_breaks_separate_alineas(self):
        alineas = split_alineas(
            "Premier alinea du texte.\n\nSecond alinea du texte.",
            generic_ruleset().alinea_pattern,
        )
        assert len(alineas) == 2

    def test_single_sentence_is_not_split(self):
        """On ne coupe pas arbitrairement une phrase juridique (§20)."""
        text = "Le contrat de travail est conclu librement entre les parties."
        assert len(split_alineas(text, generic_ruleset().alinea_pattern)) == 1

    def test_no_text_is_lost_in_alineas(self, config):
        result = parse(
            [
                "Article 5 : Sont vises :\n"
                "1° les salaries ;\n"
                "2° les apprentis."
            ],
            config,
        )
        article = result.articles[0]
        joined = " ".join(a.text for a in article.alineas)
        for word in ("salaries", "apprentis", "vises"):
            assert word in joined


# ---------------------------------------------------------------------------
# Numérotation (§24)
# ---------------------------------------------------------------------------


class TestNumberingCheck:
    def test_continuous_sequence_is_clean(self, config):
        result = parse(
            [
                "Article 1er : Premiere disposition du texte.\n"
                "Article 2 : Seconde disposition du texte.\n"
                "Article 3 : Troisieme disposition du texte."
            ],
            config,
        )
        assert check_numbering(result.articles) == []

    def test_gap_is_reported(self, config):
        """1, 3 doit être signalé comme anomalie potentielle."""
        result = parse(
            [
                "Article 1er : Premiere disposition du texte.\n"
                "Article 3 : Troisieme disposition du texte."
            ],
            config,
        )
        anomalies = check_numbering(result.articles)
        assert len(anomalies) == 1
        assert "2 manquant" in anomalies[0]

    def test_wide_gap_is_described_as_a_range(self, config):
        result = parse(
            [
                "Article 1er : Premiere disposition du texte.\n"
                "Article 6 : Sixieme disposition du texte."
            ],
            config,
        )
        assert "2 à 5" in check_numbering(result.articles)[0]

    def test_decreasing_numbering_is_reported(self, config):
        result = parse(
            [
                "Article 5 : Cinquieme disposition du texte.\n"
                "Article 2 : Deuxieme disposition du texte."
            ],
            config,
        )
        assert "non croissante" in check_numbering(result.articles)[0]

    def test_bis_does_not_count_as_a_gap(self, config):
        result = parse(
            [
                "Article 1er : Premiere disposition du texte.\n"
                "Article 1er bis : Disposition intercalaire du texte.\n"
                "Article 2 : Seconde disposition du texte."
            ],
            config,
        )
        assert check_numbering(result.articles) == []


# ---------------------------------------------------------------------------
# Sommaire et fin de partie normative
# ---------------------------------------------------------------------------


class TestNoise:
    def test_table_of_contents_does_not_create_articles(self, config):
        result = parse(
            [
                "SOMMAIRE\n"
                "Article 1er ............ 3\n"
                "Article 2 ............ 4\n"
                "Article 1er : Veritable premiere disposition du texte."
            ],
            config,
        )
        assert result.article_count == 1
        assert "Veritable" in result.articles[0].text

    def test_signature_block_ends_the_normative_part(self, config):
        result = parse(
            [
                "Article 1er : Unique disposition du texte juridique.\n"
                "Fait a Cotonou, le 10 fevrier 2026\n"
                "Article 99 : mention parasite apres signature."
            ],
            config,
            jurisdiction="benin",
        )
        assert [a.article_number for a in result.articles] == ["1er"]

    def test_annexes_survive_the_signature_block(self, config):
        result = parse(
            [
                "Article 1er : Unique disposition du texte juridique.\n"
                "Fait a Cotonou, le 10 fevrier 2026\n"
                "ANNEXE I\n"
                "Article 1er : Disposition annexe du texte."
            ],
            config,
            jurisdiction="benin",
        )
        assert any(n.level is StructureLevel.ANNEXE for n in result.structure)


# ---------------------------------------------------------------------------
# Modularité et juridictions (§29)
# ---------------------------------------------------------------------------


class TestJurisdictions:
    def test_registry_lists_installed_jurisdictions(self):
        assert {"benin", "generic"} <= set(available_jurisdictions())

    def test_unknown_jurisdiction_raises_with_hints(self):
        with pytest.raises(JurisdictionError, match="disponibles"):
            get_jurisdiction("atlantide")

    def test_unknown_jurisdiction_falls_back_in_config(self, config):
        """Un pipeline ne doit pas se bloquer sur une juridiction inconnue."""
        cfg = config.with_overrides({"project": {"jurisdiction": "atlantide"}})
        assert get_ruleset(cfg).name == "generic"

    def test_benin_rules_extend_the_generic_core(self):
        generic_names = {r.name for r in generic_ruleset().all_rules()}
        benin_names = {r.name for r in get_jurisdiction("benin").ruleset.all_rules()}
        assert generic_names < benin_names, "le socle générique est conservé"
        assert any(name.startswith("benin_") for name in benin_names)

    def test_benin_article_unique(self, config):
        result = parse(["Article unique : La presente loi entre en vigueur."], config, "benin")
        assert result.articles[0].article_number == "unique"

    def test_benin_article_nouveau(self, config):
        result = parse(
            ["Article 45 nouveau : Le contrat est desormais soumis a autorisation."],
            config,
            "benin",
        )
        assert result.articles[0].article_number == "45"

    def test_generic_ruleset_ignores_benin_specific_forms(self, config):
        """La preuve que les règles nationales sont bien isolées du socle."""
        result = parse(["Article unique : La presente loi entre en vigueur."], config)
        assert result.article_count == 0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfigurability:
    def test_article_detection_can_be_disabled(self, config):
        cfg = config.with_overrides({"parser": {"detect_articles": False}})
        result = parse_document(make_pages(["Article 1er : disposition."]), "doc", cfg)
        assert result.articles == []

    def test_hierarchy_detection_can_be_disabled(self, config):
        cfg = config.with_overrides({"parser": {"detect_hierarchy": False}})
        result = parse_document(
            make_pages(["TITRE II\nArticle 1er : disposition du texte."]), "doc", cfg
        )
        assert result.structure == []
        assert result.article_count == 1

    def test_alinea_detection_can_be_disabled(self, config):
        cfg = config.with_overrides({"parser": {"detect_alineas": False}})
        result = parse_document(
            make_pages(["Article 1er : chapeau\n1° premier\n2° second"]), "doc", cfg
        )
        assert result.articles[0].alineas == []

    def test_min_article_chars_comes_from_config(self, config):
        cfg = config.with_overrides({"parser": {"min_article_chars": 500}})
        result = parse_document(
            make_pages(["Article 1er : disposition courte."]), "doc", cfg
        )
        assert "article_potentiellement_incomplet" in result.articles[0].warnings


class TestEndToEnd:
    def test_real_pdf_pipeline(self, text_pdf, config):
        """PDF → extraction → nettoyage → articles, sur un vrai fichier."""
        from bldp.core.cleaning.normalizer import clean_pages
        from bldp.core.extraction.pymupdf_extractor import extract_document

        extraction = extract_document(text_pdf, "loi_2026_001")
        pages, _ = clean_pages(extraction.pages, config, "loi_2026_001")
        result = parse_document(pages, "loi_2026_001", config, get_ruleset(config))

        assert [a.article_number for a in result.articles] == ["1er", "2", "3", "4", "5"]
        assert result.articles[0].title == "TITRE PREMIER"
        assert result.articles[0].chapter == "CHAPITRE I"
        assert result.articles[2].chapter == "CHAPITRE II"
        assert result.articles[2].section == "Section 1"
        assert result.articles[2].page_start == 2
        assert check_numbering(result.articles) == []


class TestSignatureBoundary:
    """La formule de promulgation ne doit pas contaminer le dernier article."""

    def test_signature_block_does_not_end_up_inside_the_last_article(self, config):
        result = parse(
            [
                "Article 4 : Le contrat a duree determinee ne peut exceder quatre ans.\n"
                "Article 5 : Est puni d'une amende de 500 000 francs CFA tout employeur "
                "qui ne respecte pas les dispositions de l'article 4.\n"
                "Fait a Cotonou, le 10 fevrier 2026\n"
                "Le President de la Republique"
            ],
            config,
            jurisdiction="benin",
        )
        dernier = result.articles[-1]
        assert dernier.article_number == "5"
        assert "500 000 francs CFA" in dernier.text, "le contenu normatif est intact"
        assert "Fait a Cotonou" not in dernier.text
        assert "President de la Republique" not in dernier.text

    def test_the_epilogue_is_kept_not_discarded(self, config):
        """Rien n'est jeté : le bloc de signature est conservé à part."""
        result = parse(
            [
                "Article 1er : Unique disposition du present texte juridique.\n"
                "Fait a Cotonou, le 10 fevrier 2026"
            ],
            config,
            jurisdiction="benin",
        )
        assert "Fait a Cotonou" in result.epilogue
        assert "Fait a Cotonou" not in result.articles[0].text

    def test_a_document_without_signature_has_no_epilogue(self, config):
        result = parse(["Article 1er : Unique disposition du present texte."], config)
        assert result.epilogue == ""

    def test_annexes_after_the_signature_are_still_parsed(self, config):
        """Une annexe qui suit la promulgation reste rattachée au document."""
        result = parse(
            [
                "Article 1er : Unique disposition du present texte juridique.\n"
                "Fait a Cotonou, le 10 fevrier 2026\n"
                "ANNEXE I\n"
                "Article 1er : Disposition annexe du present texte."
            ],
            config,
            jurisdiction="benin",
        )
        assert any(n.level is StructureLevel.ANNEXE for n in result.structure)
        assert len(result.articles) == 2
