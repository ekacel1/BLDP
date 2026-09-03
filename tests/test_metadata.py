"""Tests du module 7 — métadonnées (§12).

Le fil conducteur : chaque valeur doit être justifiée, et rien ne doit être
inventé. Les tests vérifient donc autant ce qui est trouvé que ce qui est
**laissé vide et signalé**.
"""

from __future__ import annotations

import pytest

from bldp.core.metadata.engine import (
    apply_manual_metadata,
    detect_authority,
    detect_date,
    detect_document_type,
    detect_legal_domain,
    detect_number,
    detect_title,
    extract_metadata,
    find_sidecar,
    iter_missing_fields,
    metadata_completeness,
    normalize_date,
)
from bldp.jurisdictions.registry import get_jurisdiction
from bldp.models import DocumentMetadata, DocumentType, LegalStatus, Page, SourceFile
from bldp.utils import utc_now_iso


ENTETE_LOI = (
    "REPUBLIQUE DU BENIN\n"
    "ASSEMBLEE NATIONALE\n"
    "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
    "portant organisation du travail en Republique du Benin\n"
    "\n"
    "L'Assemblee nationale a delibere et adopte en sa seance du 5 fevrier 2026.\n"
)


@pytest.fixture
def benin():
    return get_jurisdiction("benin")


def make_pages(texts: list[str]) -> list[Page]:
    return [
        Page(document_id="doc", page=i + 1, text=t, source_file="doc.pdf")
        for i, t in enumerate(texts)
    ]


def make_source(tmp_path, filename="loi_2026_001.pdf", category="lois") -> SourceFile:
    path = tmp_path / filename
    path.write_bytes(b"%PDF-1.4")
    return SourceFile(
        document_id="loi_2026_001",
        source_path=str(path),
        filename=filename,
        extension=".pdf",
        size_bytes=8,
        file_hash="a" * 64,
        ingested_at=utc_now_iso(),
        category=category,
    )


# ---------------------------------------------------------------------------
# Détecteurs unitaires
# ---------------------------------------------------------------------------


class TestDateDetection:
    def test_official_date_form(self, benin):
        iso, confidence, evidence = detect_date(ENTETE_LOI, benin)
        assert iso == "2026-02-10"
        assert confidence >= 0.90
        assert "10 FEVRIER 2026" in evidence.upper()

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("du 1er janvier 2020", "2020-01-01"),
            ("du 31 decembre 1999", "1999-12-31"),
            ("le 15 aout 2021", "2021-08-15"),
            ("15/08/2021", "2021-08-15"),
        ],
    )
    def test_various_forms(self, text, expected, benin):
        assert detect_date(text, benin)[0] == expected

    def test_no_date_returns_none(self, benin):
        """Aucune date lisible : on n'en invente pas."""
        assert detect_date("Un texte sans aucune date.", benin)[0] is None

    @pytest.mark.parametrize(
        "day, month, year",
        [("32", "janvier", "2026"), ("10", "brumaire", "2026"), ("10", "janvier", "999")],
    )
    def test_impossible_dates_are_rejected(self, day, month, year):
        assert normalize_date(day, month, year) is None


class TestNumberDetection:
    def test_official_number(self, benin):
        number, confidence, _ = detect_number(ENTETE_LOI, benin)
        assert number == "2026-001"
        assert confidence >= 0.90

    def test_compound_number(self, benin):
        number, _, _ = detect_number("DECRET N° 2020-113/PR/MTFP du 4 mars 2020", benin)
        assert number.startswith("2020-113")

    def test_no_number_returns_none(self, benin):
        assert detect_number("Code du travail", benin)[0] is None


class TestTypeDetection:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("LOI N° 2026-001 du 10 fevrier 2026", DocumentType.LOI),
            ("DECRET N° 2020-113 du 4 mars 2020", DocumentType.DECRET),
            ("ARRETE N° 145 du 2 juin 2021", DocumentType.ARRETE),
            ("ORDONNANCE N° 2021-02", DocumentType.ORDONNANCE),
            ("Code du travail de la Republique du Benin", DocumentType.CODE),
            ("Constitution de la Republique du Benin", DocumentType.CONSTITUTION),
            ("Cour constitutionnelle - DECISION DCC 21-001", DocumentType.JURISPRUDENCE),
        ],
    )
    def test_from_text(self, text, expected, benin):
        doc_type, confidence, _ = detect_document_type(text, benin)
        assert doc_type is expected
        assert confidence >= 0.85

    def test_folder_is_a_weaker_hint_than_text(self, benin):
        doc_type, confidence, evidence = detect_document_type(
            "Un texte sans en-tete reconnaissable", benin, category="decrets"
        )
        assert doc_type is DocumentType.DECRET
        assert confidence < 0.60
        assert "classement manuel" in evidence

    def test_filename_is_the_weakest_hint(self, benin):
        doc_type, confidence, _ = detect_document_type(
            "texte sans en-tete", benin, category="autres", filename="arrete_145.pdf"
        )
        assert doc_type is DocumentType.ARRETE
        assert confidence < 0.45

    def test_a_citation_does_not_requalify_the_document(self, benin):
        """Un décret « portant application de la loi n° … » reste un décret.

        L'intitulé d'un texte précède ses citations : c'est la correspondance
        la plus précoce qui fait foi, sinon une simple référence requalifierait
        le document — avec les conséquences juridiques que cela implique.
        """
        texte = (
            "REPUBLIQUE DU BENIN\n"
            "DECRET N° 2026-113 DU 4 MARS 2026\n"
            "portant application de la loi sur le travail\n\n"
            "Article 1er : Le present decret fixe les modalites d'application "
            "de la loi n° 2026-001 du 10 fevrier 2026."
        )
        doc_type, _, evidence = detect_document_type(texte, benin)
        assert doc_type is DocumentType.DECRET
        assert "DECRET" in evidence.upper()

    def test_arrete_citing_a_law_stays_an_arrete(self, benin):
        texte = "ARRETE N° 145 du 2 juin 2021 pris en application de la loi n° 2020-001"
        assert detect_document_type(texte, benin)[0] is DocumentType.ARRETE

    def test_unknown_stays_unknown(self, benin):
        doc_type, confidence, _ = detect_document_type("Note de service interne", benin)
        assert doc_type is DocumentType.INCONNU
        assert confidence == 0.0


class TestAuthorityAndTitle:
    def test_authority(self, benin):
        authority, confidence, _ = detect_authority(ENTETE_LOI, benin)
        assert authority == "Assemblée nationale"
        assert confidence >= 0.70

    def test_title_from_portant_line(self):
        title, confidence, _ = detect_title(ENTETE_LOI, DocumentType.LOI, "2026-001")
        assert "organisation du travail" in title.lower()
        assert confidence >= 0.80

    def test_title_falls_back_with_low_confidence(self):
        text = "CODE DU TRAVAIL\n\nDispositions generales"
        title, confidence, _ = detect_title(text, DocumentType.CODE, None)
        assert title
        assert confidence < 0.50, "un titre de repli doit rester peu fiable"

    def test_republic_line_is_not_a_title(self):
        title, _, _ = detect_title(
            "REPUBLIQUE DU BENIN\nCODE DES PERSONNES ET DE LA FAMILLE",
            DocumentType.CODE,
            None,
        )
        assert "REPUBLIQUE DU BENIN" not in (title or "").upper()


class TestLegalDomain:
    def test_dominant_domain(self):
        text = "contrat de travail employeur salarie syndicat travail salarie"
        domain, confidence, _ = detect_legal_domain(text)
        assert domain == "travail"
        assert confidence > 0

    def test_no_keyword_no_domain(self):
        assert detect_legal_domain("texte neutre sans terme juridique")[0] is None

    def test_ambiguous_domain_has_low_confidence(self):
        _, confidence, _ = detect_legal_domain("travail penal")
        assert confidence <= 0.30


# ---------------------------------------------------------------------------
# Assemblage complet
# ---------------------------------------------------------------------------


class TestExtractMetadata:
    def test_minimal_fields_of_the_spec(self, config, tmp_path):
        """§12 : document_id, titre, type, numéro, date, juridiction, langue..."""
        metadata = extract_metadata(
            "loi_2026_001", make_pages([ENTETE_LOI]), config, make_source(tmp_path)
        )
        assert metadata.document_id == "loi_2026_001"
        assert metadata.type is DocumentType.LOI
        assert metadata.number == "2026-001"
        assert metadata.date == "2026-02-10"
        assert metadata.jurisdiction == "Benin"
        assert metadata.language == "fr"
        assert metadata.authority == "Assemblée nationale"
        assert "travail" in metadata.title.lower()
        assert metadata.retrieved_at

    def test_every_guess_carries_confidence_and_evidence(self, config, tmp_path):
        """Le pipeline doit pouvoir dire d'où vient chaque valeur."""
        metadata = extract_metadata(
            "loi_2026_001", make_pages([ENTETE_LOI]), config, make_source(tmp_path)
        )
        for field in ("type", "number", "date", "title"):
            assert field in metadata.confidence, f"{field} sans score de confiance"
            assert 0 < metadata.confidence[field] <= 1
            assert metadata.evidence.get(field), f"{field} sans preuve"

    def test_nothing_is_invented_when_absent(self, config, tmp_path):
        metadata = extract_metadata(
            "note", make_pages(["Note interne de service."]), config, make_source(tmp_path, "note.pdf", "autres")
        )
        assert metadata.number is None
        assert metadata.date is None
        assert metadata.type is DocumentType.INCONNU
        assert metadata.warnings, "l'absence doit être signalée"
        assert any("numéro officiel introuvable" in w for w in metadata.warnings)

    def test_status_defaults_to_unknown_not_in_force(self, config, tmp_path):
        """§13 : supposer « en vigueur » serait une erreur juridique."""
        metadata = extract_metadata(
            "loi", make_pages([ENTETE_LOI]), config, make_source(tmp_path)
        )
        assert metadata.status is LegalStatus.INCONNU

    def test_low_confidence_fields_are_flagged(self, config, tmp_path):
        metadata = extract_metadata(
            "doc",
            make_pages(["CODE DU TRAVAIL\n\nDispositions generales du code."]),
            config,
            make_source(tmp_path, "code.pdf", "codes"),
        )
        assert any("faible confiance" in w for w in metadata.warnings)

    def test_pdf_container_metadata_is_a_weak_fallback(self, config, tmp_path):
        metadata = extract_metadata(
            "doc",
            make_pages(["...."]),
            config,
            make_source(tmp_path, "doc.pdf", "autres"),
            pdf_metadata={"pdf_title": "Titre issu du scanner"},
        )
        assert metadata.title == "Titre issu du scanner"
        assert metadata.confidence["title"] <= 0.30

    def test_empty_document_does_not_crash(self, config, tmp_path):
        metadata = extract_metadata("vide", make_pages([""]), config, make_source(tmp_path))
        assert metadata.document_id == "vide"
        assert metadata.warnings


# ---------------------------------------------------------------------------
# Correction humaine (§16)
# ---------------------------------------------------------------------------


class TestManualOverride:
    def test_sidecar_yaml_is_detected_and_wins(self, config, tmp_path):
        source = make_source(tmp_path)
        sidecar = tmp_path / "loi_2026_001.meta.yaml"
        sidecar.write_text(
            "title: Titre officiel verifie par un juriste\n"
            "number: 2026-999\n"
            "source_url: https://sgg.gouv.bj/loi-2026-001\n"
            "status: en_vigueur\n",
            encoding="utf-8",
        )
        assert find_sidecar(source.source_path) == sidecar

        metadata = extract_metadata("loi_2026_001", make_pages([ENTETE_LOI]), config, source)
        assert metadata.title == "Titre officiel verifie par un juriste"
        assert metadata.number == "2026-999", "la saisie manuelle écrase la détection"
        assert metadata.status is LegalStatus.EN_VIGUEUR
        assert metadata.confidence["number"] == 1.0
        assert str(sidecar) in metadata.evidence["number"]

    def test_sidecar_json(self, config, tmp_path):
        source = make_source(tmp_path)
        (tmp_path / "loi_2026_001.meta.json").write_text(
            '{"title": "Titre JSON", "authority": "Conseil des ministres"}', encoding="utf-8"
        )
        metadata = extract_metadata("loi_2026_001", make_pages([ENTETE_LOI]), config, source)
        assert metadata.title == "Titre JSON"
        assert metadata.authority == "Conseil des ministres"

    def test_broken_sidecar_is_reported_not_fatal(self, config, tmp_path):
        source = make_source(tmp_path)
        (tmp_path / "loi_2026_001.meta.json").write_text("{ pas du json", encoding="utf-8")
        metadata = extract_metadata("loi_2026_001", make_pages([ENTETE_LOI]), config, source)
        assert any("illisible" in w for w in metadata.warnings)
        assert metadata.number == "2026-001", "la détection automatique reste utilisable"

    def test_french_aliases_are_accepted(self):
        metadata = DocumentMetadata(document_id="doc")
        apply_manual_metadata(metadata, {"titre": "Mon titre", "autorite": "SGG"})
        assert metadata.title == "Mon titre"
        assert metadata.authority == "SGG"

    def test_invalid_enum_value_is_reported_not_applied(self):
        metadata = DocumentMetadata(document_id="doc")
        apply_manual_metadata(metadata, {"type": "quelque_chose"})
        assert metadata.type is DocumentType.INCONNU
        assert any("type de document inconnu" in w for w in metadata.warnings)


class TestCompleteness:
    def test_full_metadata_scores_high(self):
        metadata = DocumentMetadata(
            document_id="doc",
            title="Titre",
            type=DocumentType.LOI,
            number="2026-001",
            date="2026-02-10",
            source="SGG",
        )
        assert metadata_completeness(metadata) == 1.0

    def test_empty_metadata_scores_low(self):
        assert metadata_completeness(DocumentMetadata(document_id="doc")) < 0.5

    def test_missing_fields_are_listed_for_review(self):
        metadata = DocumentMetadata(document_id="doc", title="Titre")
        missing = list(iter_missing_fields(metadata))
        assert "number" in missing and "date" in missing and "title" not in missing


class TestOnRealPdf:
    def test_end_to_end(self, text_pdf, config):
        from bldp.core.cleaning.normalizer import clean_pages
        from bldp.core.extraction.pymupdf_extractor import (
            extract_document,
            extract_pdf_metadata,
        )
        from bldp.core.loader import build_source_file

        source = build_source_file(text_pdf, text_pdf.parent)
        extraction = extract_document(text_pdf, source.document_id)
        pages, _ = clean_pages(extraction.pages, config, source.document_id)
        metadata = extract_metadata(
            source.document_id, pages, config, source, extract_pdf_metadata(text_pdf)
        )
        assert metadata.type is DocumentType.LOI
        assert metadata.number == "2026-001"
        assert metadata.date == "2026-02-10"
        assert metadata.authority is None or isinstance(metadata.authority, str)


class TestTitleAfterCleaning:
    """Le titre doit survivre à la fusion de lignes opérée par le nettoyage."""

    def test_merged_number_and_object_line(self):
        """Régression : le nettoyage recolle « LOI N° … » et « portant … ».

        La détection exigeait « portant » en début de ligne ; une fois les deux
        lignes fusionnées, elle retombait sur « ASSEMBLEE NATIONALE ».
        """
        texte = (
            "REPUBLIQUE DU BENIN\n"
            "ASSEMBLEE NATIONALE\n"
            "LOI N° 2026-001 DU 10 FEVRIER 2026 portant organisation du travail "
            "en Republique du Benin\n"
        )
        titre, confiance, _ = detect_title(texte, DocumentType.LOI, "2026-001")
        assert "organisation du travail" in titre.lower()
        assert "assemblee nationale" not in titre.lower()
        assert confiance >= 0.80

    def test_unmerged_lines_still_work(self):
        texte = (
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
            "portant organisation du travail\n"
        )
        titre, _, _ = detect_title(texte, DocumentType.LOI, "2026-001")
        assert "organisation du travail" in titre.lower()
        assert "2026-001" in titre

    def test_article_text_is_not_mistaken_for_a_title(self):
        """« portant » dans le corps d'un article n'est pas un intitulé."""
        texte = (
            "CODE DU TRAVAIL\n"
            "Article 1er : Les dispositions portant sur le repos hebdomadaire "
            "sont applicables a tous.\n"
        )
        titre, _, _ = detect_title(texte, DocumentType.CODE, None)
        assert "repos hebdomadaire" not in (titre or "").lower()


class TestOcrDashesAndCitedDates:
    """Régressions trouvées sur 20 lois béninoises réellement scannées."""

    def test_em_dash_in_the_official_number(self, benin):
        """L'OCR rend « n° 2025 - 18 » avec un cadratin (U+2014).

        Un motif limité à `-` et `–` ne reconnaissait pas le numéro du document
        et retenait celui du texte cité : la loi 2025-18 était enregistrée sous
        le numéro 2022-09.
        """
        texte = (
            "LOI n\u00b0 2025 \u2014 18 DU 25 JUILLET 2025 modifiant et completant "
            "la loi n\u00b0 2022-09 du 27 juin 2022"
        )
        assert detect_number(texte, benin)[0] == "2025-18"

    @pytest.mark.parametrize("tiret", ["-", "\u2010", "\u2012", "\u2013", "\u2014", "\u2212"])
    def test_every_unicode_dash_is_accepted(self, tiret, benin):
        assert detect_number(f"LOI n\u00b0 2025 {tiret} 18 DU 25 JUILLET 2025", benin)[0] == "2025-18"

    def test_the_cited_date_does_not_win(self, benin):
        texte = "LOI n° 2025-18 DU 25 JUILLET 2025 modifiant la loi n° 2022-09 du 27 juin 2022"
        iso, confiance, _ = detect_date(texte, benin, "2025-18")
        assert iso == "2025-07-25"
        assert confiance >= 0.90

    def test_an_unreadable_own_date_lowers_confidence(self, benin):
        """« 1FF JUILLET » (OCR de « 1er ») : la date citée est retenue…

        …mais à confiance basse et explicitement signalée. Une date fausse à
        0,95 serait pire que pas de date du tout.
        """
        texte = (
            "LOI n° 2025-11 DU 1FF JUILLET 2025 portant modification de la "
            "loi n° 2024-09 du 02 septembre 2024"
        )
        iso, confiance, preuve = detect_date(texte, benin, "2025-11")
        assert iso == "2024-09-02"
        assert confiance <= 0.60
        assert "à vérifier" in preuve

    def test_national_motto_is_not_a_title(self):
        texte = (
            "REPUBLIQUE DU BENIN\n"
            "Fraternite-Justice-Travail\n"
            "LOI n° 2025-19 DU 22 JUILLET 2025 relative aux associations\n"
        )
        titre, _, _ = detect_title(texte, DocumentType.LOI, "2025-19")
        assert "fraternite" not in titre.lower()
        assert "associations" in titre.lower()

    def test_relative_aux_is_recognised_as_an_object(self):
        """« relative aux associations » est aussi courant que « relative à »."""
        titre, confiance, _ = detect_title(
            "LOI n° 2025-19 DU 22 JUILLET 2025 relative aux associations",
            DocumentType.LOI,
            "2025-19",
        )
        assert confiance >= 0.80


# ---------------------------------------------------------------------------
# Autorité déduite du type
# ---------------------------------------------------------------------------


class TestAutoriteDeduite:
    """Combler un vide sans jamais recouvrir une lecture.

    Mesuré sur le lot 1 du corpus SGG : 842 documents sans autorité lisible,
    presque tous des lois et des ordonnances dont l'en-tête est illisible sur
    le scan. L'institution, elle, ne laisse aucun doute sur qui a pris l'acte.
    """

    @pytest.fixture
    def profil(self):
        from bldp.config import load_config
        from bldp.jurisdictions.registry import get_profile

        return get_profile(load_config())

    @pytest.mark.parametrize(
        "type_acte,attendu",
        [
            ("loi", "Assemblée nationale"),
            ("code", "Assemblée nationale"),
            ("decret", "Président de la République"),
            ("ordonnance", "Président de la République"),
        ],
    )
    def test_les_types_sans_ambiguite_se_deduisent(self, profil, type_acte, attendu):
        from bldp.core.metadata.engine import deduce_authority
        from bldp.models import DocumentType

        autorite, confiance, preuve = deduce_authority(DocumentType(type_acte), profil)
        assert autorite == attendu
        assert 0 < confiance < 1
        assert "déduit" in preuve and "non lu" in preuve

    @pytest.mark.parametrize(
        "type_acte", ["arrete", "decision", "convention", "jurisprudence", "inconnu"]
    )
    def test_les_types_ambigus_ne_se_devinent_pas(self, profil, type_acte):
        """Un arrêté peut venir d'un ministre, d'un préfet ou d'un maire.

        Mieux vaut un champ vide et signalé qu'un champ rempli et faux.
        """
        from bldp.core.metadata.engine import deduce_authority
        from bldp.models import DocumentType

        autorite, _, _ = deduce_authority(DocumentType(type_acte), profil)
        assert autorite is None

    def test_le_socle_generique_ignore_le_benin(self):
        """§29 : ce qui est vrai au Bénin ne l'est pas ailleurs."""
        from bldp.core.metadata.engine import deduce_authority
        from bldp.models import DocumentType

        assert deduce_authority(DocumentType.LOI, None) == (None, 0.0, "")

    def test_une_autorite_lue_n_est_jamais_remplacee(self, config):
        """La déduction comble un vide ; elle ne recouvre pas une lecture."""
        from bldp.core.metadata.engine import extract_metadata

        pages = make_pages([
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2024-09 DU 20 FEVRIER 2024 portant organisation.\n"
            "LA COUR CONSTITUTIONNELLE,\n\n"
            "Article 1er : La presente loi fixe les regles.\n"
        ])
        metadata = extract_metadata("loi_2024_09", pages, config)
        assert metadata.authority == "Cour constitutionnelle"
        assert "déduit" not in metadata.evidence.get("authority", "")

    def test_une_autorite_absente_se_comble_et_se_declare(self, config):
        """Le cas visé : un scan dont l'en-tête institutionnel a disparu."""
        from bldp.core.metadata.engine import extract_metadata

        pages = make_pages([
            "LOI N° 2024-09 DU 20 FEVRIER 2024 portant organisation du travail.\n\n"
            "Article 1er : La presente loi fixe les regles applicables.\n"
        ])
        metadata = extract_metadata("loi_2024_09", pages, config)
        assert metadata.authority == "Assemblée nationale"
        assert "déduit" in metadata.evidence["authority"]
        assert metadata.confidence["authority"] < 0.9, (
            "une déduction doit se distinguer d'une lecture dans les scores"
        )
