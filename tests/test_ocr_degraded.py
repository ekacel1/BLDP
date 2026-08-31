"""Régressions trouvées sur un corpus scanné de mauvaise qualité.

24 documents béninois de cinq types (accord, arrêté, décision, décret,
ordonnance) ont exposé une famille de défauts absente des scans nets : **quand
l'OCR abîme l'intitulé du document, toutes les métadonnées dérivent vers les
visas**, c'est-à-dire vers les textes *cités*.

Le fil conducteur de ces tests : un document doit parler de lui-même. Ce qu'il
cite ne le définit pas.
"""

from __future__ import annotations

import pytest

from bldp.config import load_config
from bldp.core.cleaning.normalizer import apply_ocr_fixes
from bldp.core.metadata.engine import (
    detect_date,
    detect_document_type,
    detect_number,
    strip_citation_lines,
)
from bldp.core.parser.legal_parser import parse_document
from bldp.core.parser.rules import generic_ruleset
from bldp.jurisdictions.registry import get_jurisdiction
from bldp.models import DocumentType, Page
from bldp.utils import NUMERO_PREFIX, normalize_ocr_number


@pytest.fixture
def benin():
    return get_jurisdiction("benin")


def parse(texte: str, config, jurisdiction: str = "benin"):
    pages = [Page(document_id="doc", page=1, text=texte, source_file="doc.pdf")]
    ruleset = get_jurisdiction(jurisdiction).ruleset if jurisdiction else generic_ruleset()
    return parse_document(pages, "doc", config, ruleset=ruleset)


def clean_and_parse(texte: str, config, jurisdiction: str = "benin"):
    """Nettoyage **puis** parsing, comme le fait le pipeline.

    Les corrections OCR vivent dans le module de nettoyage : un test qui
    parse directement le texte brut ne les exerce pas, et ne dirait donc rien
    de ce que produit réellement le pipeline.
    """
    from bldp.core.cleaning.normalizer import clean_pages

    pages = [Page(document_id="doc", page=1, text=texte, source_file="doc.pdf")]
    cleaned, _ = clean_pages(pages, config, "doc")
    ruleset = get_jurisdiction(jurisdiction).ruleset if jurisdiction else generic_ruleset()
    return parse_document(cleaned, "doc", config, ruleset=ruleset)


# ---------------------------------------------------------------------------
# Le symbole « ° » mal lu
# ---------------------------------------------------------------------------


class TestDegreeSignOcr:
    """L'OCR rend « N° » par « N" », « N' » ou « N. ».

    Une seule lettre mal lue suffisait à faire échouer la détection du numéro —
    et sans numéro, le type et la date se rabattaient sur les visas.
    """

    @pytest.mark.parametrize(
        "entete, attendu",
        [
            ('DECRET N" 2019 _ 230 DU 31 JUILLET 2019', "2019-230"),
            ("ORDONNANCE N'2010-01 DU 25 JUIN 2010", "2010-01"),
            ("DECRET N., 2022-293 DU 11 MAI 2022", "2022-293"),
            ("ARRETE No 2018-002 DU 2 MARS 2018", "2018-002"),
            ("DECRET n° 2026-313 DU 24 MAI 2026", "2026-313"),
        ],
    )
    def test_number_is_recognised(self, entete, attendu, benin):
        assert detect_number(entete, benin)[0] == attendu

    def test_the_prefix_does_not_eat_ordinary_words(self, benin):
        """« en 2019 », « an 2020 » ne sont pas des numéros officiels."""
        assert detect_number("adopte en 2019 puis modifie en 2020", benin)[0] is None

    @pytest.mark.parametrize(
        "brut, attendu",
        [
            ("2018 -OO1", "2018-001"),
            ("2019 _ 230", "2019-230"),
            ("2010-01", "2010-01"),
        ],
    )
    def test_confused_digits_are_repaired(self, brut, attendu):
        """« OO1 » est « 001 » — sûr uniquement dans un numéro déjà reconnu."""
        assert normalize_ocr_number(brut) == attendu

    def test_the_literal_suffix_is_preserved(self):
        """« /PR/SGG » n'est pas numérique : on n'y touche pas."""
        assert normalize_ocr_number("2018-OO1/PR/SGG18").endswith("/PR/SGG18")


# ---------------------------------------------------------------------------
# Les visas ne définissent pas le document
# ---------------------------------------------------------------------------


VISAS = (
    "REPUBLIQUE DU BENIN\n"
    "ARRETE N\" 2018-002 DU 2 MARS 2018 portant nomination\n"
    "LE PRESIDENT DE LA REPUBLIQUE,\n"
    "Vu la loi n' 90-32 du 11 decembre 1990 portant Constitution de la Republique du Benin ;\n"
    "Vu le decret n' 2017-506 du 27 octobre 2017 portant composition du Gouvernement ;\n"
    "ARRETE :\n"
)


class TestCitationsDoNotDefineTheDocument:
    def test_visa_lines_are_identified(self):
        restant = strip_citation_lines(VISAS)
        assert "Vu la loi" not in restant
        assert "Vu le decret" not in restant
        assert "ARRETE N\" 2018-002" in restant, "l'intitulé propre doit survivre"

    def test_the_type_comes_from_the_document_not_its_visas(self, config, tmp_path):
        from bldp.core.metadata.engine import extract_metadata
        from bldp.models import SourceFile
        from bldp.utils import utc_now_iso

        source = SourceFile(
            document_id="arrete", source_path=str(tmp_path / "a.pdf"), filename="a.pdf",
            extension=".pdf", size_bytes=1, file_hash="a" * 64, ingested_at=utc_now_iso(),
        )
        pages = [Page(document_id="arrete", page=1, text=VISAS, source_file="a.pdf")]
        metadata = extract_metadata("arrete", pages, config, source)

        assert metadata.type is DocumentType.ARRETE
        assert metadata.number == "2018-002"
        assert metadata.date == "2018-03-02"

    def test_the_constitution_of_a_visa_does_not_requalify(self, benin):
        """« portant Constitution de la République du Bénin » désigne la loi 90-32.

        Elle est visée par presque tous les textes béninois : sans ancrage,
        13 documents sur 24 étaient typés « constitution ».
        """
        visa = "la loi n' 90-32 du 11 decembre 1990 portant Constitution de la Republique du Benin"
        assert detect_document_type(visa, benin)[0] is not DocumentType.CONSTITUTION

    def test_a_real_constitution_is_still_detected(self, benin):
        texte = "CONSTITUTION DE LA REPUBLIQUE DU BENIN\nPREAMBULE\n"
        assert detect_document_type(texte, benin)[0] is DocumentType.CONSTITUTION

    def test_a_mentioned_court_does_not_make_it_case_law(self, benin):
        """« proclamation par la Cour constitutionnelle » figure dans des décrets."""
        texte = (
            "DECRET N\" 2019-230 DU 31 JUILLET 2019 portant nomination\n"
            "la decision portant proclamation, le 30 mars 2016 par la Cour "
            "constitutionnelle, des resultats definitifs\n"
        )
        assert detect_document_type(texte, benin)[0] is DocumentType.DECRET

    def test_a_court_as_issuer_is_still_case_law(self, benin):
        texte = "COUR CONSTITUTIONNELLE\nDECISION DCC 21-001 du 5 janvier 2021\n"
        assert detect_document_type(texte, benin)[0] is DocumentType.JURISPRUDENCE

    def test_a_date_taken_from_a_visa_is_flagged(self, benin):
        """Quand l'intitulé est illisible, la date de visa reste possible…

        …mais à confiance basse et explicitement signalée. Une date fausse à
        0,95 est pire qu'une date absente.
        """
        texte = (
            "ARRETE illisible\n"
            "Vu la loi n' 90-32 du 11 decembre 1990 portant Constitution ;\n"
        )
        iso, confiance, preuve = detect_date(texte, benin)
        assert iso == "1990-12-11"
        assert confiance <= 0.45
        assert "visa" in preuve


# ---------------------------------------------------------------------------
# Textes annexés
# ---------------------------------------------------------------------------


ORDONNANCE_AVEC_ACCORD = (
    "ORDONNANCE N'2010-01 DU 25 JUIN 2010\n"
    "portant autorisation de ratification de l'Accord de pret\n"
    "Article 1\"'\n"
    "Est autorisee la ratification de l'accord de pret.\n"
    "Article 2\n"
    "La presente ordonnance sera executee comme loi de l'Etat.\n"
    "Fait a Cotonou, le 25 juin 2010\n"
    "ARTICLE I-\n"
    "Objet de l'accord annexe et definitions applicables.\n"
    "ARTICLE II\n"
    "Engagements financiers des parties signataires.\n"
    "ARTICLE III\n"
    "Conditions d'entree en vigueur de l'accord.\n"
)


class TestAnnexedTexts:
    def test_an_annexed_agreement_is_not_lost(self, config):
        """Une ordonnance de ratification perdait les 10 articles de l'accord.

        Le texte annexé suit la promulgation sans être introduit par le mot
        « ANNEXE » : il était donc entièrement ignoré. C'est pourtant le droit
        que l'ordonnance approuve.
        """
        result = parse(ORDONNANCE_AVEC_ACCORD, config)
        numeros = [a.article_number for a in result.articles]
        assert numeros[:2] == ["1", "2"]
        assert {"I", "II", "III"} <= set(numeros)

    def test_an_isolated_stamp_after_signature_is_still_ignored(self, config):
        """Le garde-fou tient : un article isolé après signature reste écarté."""
        result = parse(
            "Article 1er : Unique disposition du present texte juridique.\n"
            "Fait a Cotonou, le 10 fevrier 2026\n"
            "Article 99 : mention parasite de tampon.\n",
            config,
        )
        assert [a.article_number for a in result.articles] == ["1er"]

    def test_the_first_article_survives_a_mangled_ordinal(self, config):
        """L'OCR rend « Article 1er » par « Article 1"' »."""
        result = parse(
            "Article 1\"'\nPremiere disposition du texte.\n"
            "Article 2\nSeconde disposition du texte.\n",
            config,
        )
        assert [a.article_number for a in result.articles] == ["1", "2"]


# ---------------------------------------------------------------------------
# Mot-clé « Article » mal reconnu
# ---------------------------------------------------------------------------


class TestArticleKeywordOcr:
    @pytest.mark.parametrize("variante", ["Articte", "Artide", "Articie", "Artic1e", "ArticIe"])
    def test_known_misreadings_are_repaired(self, variante):
        texte, count = apply_ocr_fixes(f"{variante} 36 : Comptabilite")
        assert texte.startswith("Article 36")
        assert count >= 1

    def test_ordinary_words_are_untouched(self):
        """La liste est explicite : pas de motif large qui inventerait des articles."""
        for mot in ("Artisan", "Artifice", "Articulation", "Artiste"):
            assert apply_ocr_fixes(mot)[0] == mot


# ---------------------------------------------------------------------------
# Classement en dossiers
# ---------------------------------------------------------------------------


class TestFolderCategories:
    @pytest.mark.parametrize(
        "dossier", ["decret", "decrets", "arrete", "ordonnance", "accord", "decision"]
    )
    def test_singular_and_plural_are_both_accepted(self, dossier, tmp_path):
        """Rien n'oblige l'utilisateur à deviner la convention du projet."""
        from bldp.core.loader import detect_category

        chemin = tmp_path / dossier / "x.pdf"
        chemin.parent.mkdir(parents=True)
        chemin.write_bytes(b"%PDF-1.4")
        assert detect_category(chemin, tmp_path) == dossier

    def test_an_unknown_folder_is_not_invented(self, tmp_path):
        from bldp.core.loader import detect_category

        chemin = tmp_path / "dossier_libre" / "x.pdf"
        chemin.parent.mkdir(parents=True)
        chemin.write_bytes(b"%PDF-1.4")
        assert detect_category(chemin, tmp_path) == "autres"


# ---------------------------------------------------------------------------
# Romains produits par l'OCR
# ---------------------------------------------------------------------------


class TestRomanNumeralsFromOcr:
    """L'OCR produit des suites de lettres romaines qui n'en sont pas.

    Une lecture permissive leur donnait une valeur — « ICI » valait 100 —
    qui créait ensuite de fausses ruptures de numérotation.
    """

    @pytest.mark.parametrize(
        "valeur, attendu",
        [("I", 1), ("IV", 4), ("IX", 9), ("XIV", 14), ("XV", 15), ("MCMXC", 1990)],
    )
    def test_canonical_numerals_are_read(self, valeur, attendu):
        from bldp.utils import roman_to_int

        assert roman_to_int(valeur) == attendu

    @pytest.mark.parametrize("valeur", ["ICI", "vlII", "XVX", "IIII", "VV", "XIIII"])
    def test_non_canonical_sequences_are_refused(self, valeur):
        """En cas de doute, ne pas interpréter — et le signaler (§33)."""
        from bldp.utils import roman_to_int

        assert roman_to_int(valeur) is None

    def test_an_unreadable_number_is_flagged_on_the_article(self, config):
        result = parse(
            "ARTICLE I\nPremiere disposition de l accord annexe.\n"
            "ARTICLE ICI\nDisposition dont le numero est illisible.\n",
            config,
        )
        illisible = next(a for a in result.articles if a.article_number == "ICI")
        assert illisible.numeric_value is None
        assert "numero_article_non_interpretable" in illisible.warnings


# ---------------------------------------------------------------------------
# Numéro tronqué par l'OCR
# ---------------------------------------------------------------------------


class TestTruncatedNumberIsRefused:
    def test_a_dot_dash_separator_is_accepted(self, benin):
        assert detect_number("N° 2010.-028 DU 25 JUIN", benin)[0] == "2010-028"

    def test_a_number_cut_mid_token_is_refused(self, benin):
        """« N"2olo.- Oü8 » : le « 2 » de 028 est détruit.

        Capturer « 2010-0 » donnerait un numéro **faux annoncé comme sûr**.
        Refuser renvoie le document vers la validation humaine.
        """
        assert detect_number('N"2olo.- Oü8 lpcsli', benin)[0] is None

    def test_a_literal_suffix_does_not_trigger_the_guard(self, benin):
        numero = detect_number('ARRETE N" 2018 -OO1/PR/SGG18 portant', benin)[0]
        assert numero == "2018-001/PR/SGG18"


# ---------------------------------------------------------------------------
# Annexe implicite et numérotation par portée
# ---------------------------------------------------------------------------


ORDONNANCE_DEUX_ACCORDS = (
    "ORDONNANCE N'2010-02 DU 25 JUIN 2010\n"
    "Article 1er\nEst autorisee la ratification des accords.\n"
    "Article 2\nLa presente ordonnance sera executee comme loi de l'Etat.\n"
    "Fait a Cotonou, le 25 juin 2010\n"
    "ARTICLE I\nObjet du premier accord annexe.\n"
    "ARTICLE II\nEngagements des parties au premier accord.\n"
    "ARTICLE I\nObjet du second accord annexe au present texte.\n"
    "ARTICLE II\nEngagements des parties au second accord.\n"
)


class TestImplicitAnnexScope:
    def test_an_implicit_annex_node_is_created(self, config):
        """Le texte annexé sous-entend une subdivision : on la matérialise."""
        from bldp.models import StructureLevel

        result = parse(ORDONNANCE_DEUX_ACCORDS, config)
        annexes = [n for n in result.structure if n.level is StructureLevel.ANNEXE]
        assert len(annexes) == 1
        assert "annex" in annexes[0].label.lower()

    def test_annexed_articles_carry_their_context(self, config):
        result = parse(ORDONNANCE_DEUX_ACCORDS, config)
        corps = [a for a in result.articles if a.annexe is None]
        annexes = [a for a in result.articles if a.annexe is not None]
        assert [a.article_number for a in corps] == ["1er", "2"]
        assert len(annexes) == 4
        assert all(a.hierarchy_path for a in annexes)

    def test_identifiers_no_longer_collide(self, config):
        """« Article premier » du corps et celui de l'annexe sont distincts."""
        result = parse(ORDONNANCE_DEUX_ACCORDS, config)
        ids = [a.article_id for a in result.articles]
        assert len(set(ids)) == len(ids)
        assert any("annex" in i for i in ids)

    def test_restarting_annexed_agreements_raise_no_anomaly(self, config):
        """Deux accords annexés repartent chacun de I : ce n'est pas une rupture."""
        from bldp.core.parser.legal_parser import check_numbering

        result = parse(ORDONNANCE_DEUX_ACCORDS, config)
        assert check_numbering(result.articles) == []

    def test_a_restart_in_the_body_is_still_an_anomaly(self, config):
        """La tolérance vaut pour les annexes, pas pour le corps du texte."""
        from bldp.core.parser.legal_parser import check_numbering

        result = parse(
            "Article 5 : Cinquieme disposition du present texte.\n"
            "Article 2 : Deuxieme disposition, hors sequence.\n",
            config,
        )
        assert any("non croissante" in a for a in check_numbering(result.articles))

    def test_a_real_gap_inside_an_annex_is_still_reported(self, config):
        """Tolérer les redémarrages ne doit pas masquer les vraies lacunes."""
        from bldp.core.parser.legal_parser import check_numbering

        result = parse(
            "Article 1er\nDisposition du corps.\n"
            "Article 2\nLa presente ordonnance sera executee.\n"
            "Fait a Cotonou, le 25 juin 2010\n"
            "ARTICLE I\nPremiere disposition annexee.\n"
            "ARTICLE II\nDeuxieme disposition annexee.\n"
            "ARTICLE V\nCinquieme disposition annexee, apres une lacune.\n",
            config,
        )
        anomalies = check_numbering(result.articles)
        assert any("manquant" in a for a in anomalies)


# ---------------------------------------------------------------------------
# Codes : hiérarchie profonde et couche texte issue d'un OCR antérieur
# ---------------------------------------------------------------------------


CODE_ELECTORAL = (
    "LOI N' 2019 - 43 DU 15 NOVEMBRE 2019\n"
    "porlont code electorol.\n"
    "LIVRE PRELIMINAIRE\n"
    "TITRE UNIQUE\n"
    "DES DEFINITIONS\n"
    "Article leI: Au sens du present code, on entend por centre de vote.\n"
    "TIVRE PREMIER\n"
    "DES REGLES COMMUNES AUX ELECTIONS GENERALES\n"
    "TITRE PREMIER\n"
    "DES GENERALITES\n"
    "Arlicle 2 : Les dispositions du present livre concernent les regles communes.\n"
    "Arlicle 3 : L'election est le choix libre por le peuple des citoyens.\n"
    "CHAPITREI\n"
    "DES GENERALITES\n"
    "Arlicle l3 : Les elections sont gerees par une structure administrative.\n"
    "SECfIONl\n"
    "ATTRIBUTIONS ET COMPOSITION\n"
    "Artlcle 20 : Le Conseil electoral est compose de cinq membres.\n"
)


class TestCodeWithDeepHierarchy:
    """Un **code** sollicite le parser plus qu'aucun autre texte.

    Sa hiérarchie va de LIVRE à l'article en passant par TITRE, CHAPITRE,
    SECTION et PARAGRAPHE, et sa couche texte provient souvent d'un OCR
    antérieur au pipeline.
    """

    def test_the_livre_level_is_detected(self, config):
        """« TIVRE » (L lu T) faisait disparaître tout le niveau LIVRE."""
        from bldp.models import StructureLevel

        result = clean_and_parse(CODE_ELECTORAL, config)
        livres = [n for n in result.structure if n.level is StructureLevel.LIVRE]
        assert len(livres) == 2

    def test_glued_keywords_are_separated(self, config):
        """« CHAPITREI », « SECfIONl » : l'OCR perd l'espace avant le numéro."""
        from bldp.models import StructureLevel

        result = clean_and_parse(CODE_ELECTORAL, config)
        niveaux = {n.level for n in result.structure}
        assert StructureLevel.CHAPITRE in niveaux
        assert StructureLevel.SECTION in niveaux

    def test_every_article_variant_is_recovered(self, config):
        """« Arlicle », « Artlcle », « Article leI » comptent tous."""
        result = clean_and_parse(CODE_ELECTORAL, config)
        numeros = [a.article_number for a in result.articles]
        assert numeros == ["1er", "2", "3", "13", "20"]

    def test_articles_carry_the_full_hierarchy(self, config):
        result = clean_and_parse(CODE_ELECTORAL, config)
        dernier = result.articles[-1]
        assert dernier.livre and dernier.title
        assert dernier.chapter and dernier.section
        assert len(dernier.hierarchy_path) >= 4
