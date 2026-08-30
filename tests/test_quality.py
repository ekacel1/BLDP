"""Tests des modules 9 et 10 — doublons (§14) et contrôle qualité (§15-16)."""

from __future__ import annotations

import pytest

from bldp.core.dedup import (
    containment,
    document_text_hash,
    find_duplicate_pages,
    find_duplicates,
    jaccard,
    shingles,
)
from bldp.core.validation.quality import (
    comparison_view,
    evaluate,
    evaluate_all,
    readability_score,
    review_queue,
    suggest_validation,
    summarize,
    suspect_char_ratio,
)
from bldp.models import (
    Article,
    Document,
    DocumentMetadata,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    Page,
    PdfAnalysis,
    QualityStatus,
    SourceFile,
    ValidationStatus,
)
from bldp.utils import utc_now_iso

BON_TEXTE = (
    "Article 1er : La presente loi fixe les regles applicables aux relations "
    "de travail entre les employeurs et les travailleurs dans le secteur prive. "
    "Elle est applicable sur toute l'etendue du territoire national."
)


def build_document(
    document_id: str = "doc",
    page_texts: list[str] | None = None,
    articles: int = 3,
    file_hash: str | None = None,
    ocr_required: bool = False,
    method: ExtractionMethod = ExtractionMethod.NATIVE,
    ocr_confidence: float | None = None,
    with_metadata: bool = True,
) -> Document:
    page_texts = page_texts if page_texts is not None else [BON_TEXTE, BON_TEXTE + " Suite."]
    source = SourceFile(
        document_id=document_id,
        source_path=f"/input/{document_id}.pdf",
        filename=f"{document_id}.pdf",
        extension=".pdf",
        size_bytes=1000,
        file_hash=file_hash or (document_id * 8)[:64].ljust(64, "0"),
        ingested_at=utc_now_iso(),
    )
    metadata = DocumentMetadata(
        document_id=document_id,
        title="Loi portant organisation du travail" if with_metadata else None,
        type=DocumentType.LOI if with_metadata else DocumentType.INCONNU,
        number="2026-001" if with_metadata else None,
        date="2026-02-10" if with_metadata else None,
        source="SGG" if with_metadata else None,
        confidence={"number": 0.92, "date": 0.95} if with_metadata else {},
    )
    pages = [
        Page(
            document_id=document_id,
            page=index + 1,
            text=text,
            raw_text=f"EN-TETE\n{text}\n{index + 1}",
            source_file=source.filename,
            method=method,
            ocr_confidence=ocr_confidence,
        )
        for index, text in enumerate(page_texts)
    ]
    article_list = [
        Article(
            article_id=f"{document_id}_article_{n}",
            document_id=document_id,
            article_number=str(n),
            text=f"Contenu suffisamment long de l'article {n} pour ne pas alerter.",
            label=f"Article {n}",
            position=n - 1,
            page_start=1,
            page_end=1,
            numeric_value=float(n),
            hierarchy_path=["TITRE I"],
            title="TITRE I",
        )
        for n in range(1, articles + 1)
    ]
    return Document(
        document_id=document_id,
        source=source,
        metadata=metadata,
        analysis=PdfAnalysis(
            document_id=document_id,
            pages=len(pages),
            size_bytes=1000,
            has_text=not ocr_required,
            ocr_required=ocr_required,
            confidence=0.95,
        ),
        extraction=ExtractionResult(
            document_id=document_id,
            source_file=source.filename,
            method=method,
            pages=pages,
        ),
        articles=article_list,
    )


# ---------------------------------------------------------------------------
# Module 9 — doublons
# ---------------------------------------------------------------------------


class TestSimilarityPrimitives:
    def test_identical_texts_have_jaccard_one(self):
        assert jaccard(shingles(BON_TEXTE), shingles(BON_TEXTE)) == pytest.approx(1.0)

    def test_unrelated_texts_have_low_jaccard(self):
        other = "Le present decret fixe les modalites de creation des societes commerciales."
        assert jaccard(shingles(BON_TEXTE), shingles(other)) < 0.1

    def test_containment_detects_inclusion(self):
        # Le remplissage doit être *varié* : répéter la même phrase ne crée
        # qu'une poignée de shingles distincts et ne simule pas un recueil.
        filler = " ".join(
            f"Article {n} : disposition numero {n} portant sur un sujet distinct."
            for n in range(10, 90)
        )
        extract = shingles(BON_TEXTE)
        recueil = shingles(BON_TEXTE + " " + filler)
        assert containment(extract, recueil) > 0.9
        assert jaccard(extract, recueil) < 0.5, "Jaccard seul raterait cette inclusion"

    def test_text_hash_ignores_formatting(self):
        a = build_document("a", page_texts=["Article 1er   :  texte"])
        b = build_document("b", page_texts=["article 1er : TEXTE"])
        assert document_text_hash(a) == document_text_hash(b)


class TestDuplicateDetection:
    def test_identical_files(self, config):
        a = build_document("a", file_hash="h" * 64)
        b = build_document("b", file_hash="h" * 64)
        report = find_duplicates([a, b], config)
        assert report.identical_files == 1
        assert b.duplicates[0].duplicate_of == "a"
        assert b.duplicates[0].kind == "file_hash"

    def test_same_text_different_files(self, config):
        """Même document, noms et fichiers différents (§14)."""
        a = build_document("a", file_hash="1" * 64)
        b = build_document("b", file_hash="2" * 64)
        report = find_duplicates([a, b], config)
        assert report.identical_texts == 1
        assert b.duplicates[0].kind == "text_hash"

    def test_similar_but_not_identical_versions(self, config):
        base = BON_TEXTE + " " + "Disposition complementaire numero un. " * 10
        variant = base + " Article 12 : disposition ajoutee par la loi modificative."
        a = build_document("a", page_texts=[base], file_hash="1" * 64)
        b = build_document("b", page_texts=[variant], file_hash="2" * 64)
        report = find_duplicates([a, b], config.with_overrides({"dedup": {"similarity_threshold": 0.8}}))
        assert report.similar_pairs == 1
        link = (a.duplicates + b.duplicates)[0]
        assert link.kind == "similarity"
        assert 0.8 <= link.similarity < 1.0
        assert "vérification humaine" in link.details

    def test_partial_inclusion(self, config):
        extract = BON_TEXTE
        recueil = BON_TEXTE + " " + "Texte sans rapport dans un recueil. " * 60
        a = build_document("extrait", page_texts=[extract], file_hash="1" * 64)
        b = build_document("recueil", page_texts=[recueil], file_hash="2" * 64)
        report = find_duplicates([a, b], config)
        assert report.partial_pairs == 1
        assert a.duplicates[0].kind == "partial"
        assert a.duplicates[0].duplicate_of == "recueil"

    def test_distinct_documents_are_not_linked(self, config):
        a = build_document("a", page_texts=["Le present decret organise les marches publics."], file_hash="1" * 64)
        b = build_document("b", page_texts=["La presente loi porte statut de la fonction publique."], file_hash="2" * 64)
        report = find_duplicates([a, b], config)
        assert report.total_links == 0

    def test_documents_are_never_deleted(self, config):
        """§14 : on marque, on ne supprime jamais."""
        a = build_document("a", file_hash="h" * 64)
        b = build_document("b", file_hash="h" * 64)
        documents = [a, b]
        find_duplicates(documents, config)
        assert len(documents) == 2, "les deux documents restent dans le corpus"
        assert b.duplicates and b.full_text, "le contenu du doublon est conservé"

    def test_delete_action_is_refused(self, config):
        cfg = config.with_overrides({"dedup": {"action": "delete"}})
        report = find_duplicates([build_document("a")], cfg)
        assert any("jamais supprimés" in w for w in report.warnings)

    def test_duplicates_against_existing_corpus(self, config):
        """Un doublon d'un document déjà en base est reconnu."""
        new = build_document("nouveau", file_hash="h" * 64)
        report = find_duplicates([new], config, known_file_hashes={"h" * 64: "ancien"})
        assert report.identical_files == 1
        assert new.duplicates[0].duplicate_of == "ancien"

    def test_empty_batch(self, config):
        assert find_duplicates([], config).total_links == 0

    def test_duplicate_pages_inside_a_document(self):
        page = "Article 1er : " + "disposition repetee dans le document. " * 12
        document = build_document("doc", page_texts=[page, "Autre contenu. " * 30, page])
        assert find_duplicate_pages(document) == [3]

    def test_short_pages_are_not_treated_as_duplicates(self):
        document = build_document("doc", page_texts=["1", "2", "1"])
        assert find_duplicate_pages(document) == []


# ---------------------------------------------------------------------------
# Mesures de qualité
# ---------------------------------------------------------------------------


class TestQualityMeasures:
    def test_clean_french_text_is_readable(self):
        assert readability_score(BON_TEXTE) > 0.7

    def test_garbled_text_is_not(self):
        assert readability_score("#@$ %^& *() _+= {}[] |\\<>~`") < 0.3

    def test_fragmented_ocr_is_penalised(self):
        fragmented = "L e c o n t r a t d e t r a v a i l e s t c o n c l u"
        assert readability_score(fragmented) < readability_score(BON_TEXTE)

    def test_empty_text_scores_zero(self):
        assert readability_score("   ") == 0.0

    def test_suspect_chars(self):
        assert suspect_char_ratio("texte normal") == 0.0
        assert suspect_char_ratio("te�te ���") > 0.2


# ---------------------------------------------------------------------------
# Module 10 — rapport qualité
# ---------------------------------------------------------------------------


class TestQualityReport:
    def test_clean_document_scores_high(self, config):
        report = evaluate(build_document(), config)
        assert report.score > 0.85
        assert report.status is QualityStatus.OK
        assert report.articles_detected == 3
        assert report.empty_pages == 0

    def test_report_shape_matches_the_spec(self, config):
        """§15 : ocr_quality, text_quality, articles_detected, possible_errors…"""
        payload = evaluate(build_document(), config).to_dict()
        assert set(payload).issuperset(
            {
                "document_id", "ocr_quality", "text_quality", "articles_detected",
                "possible_errors", "missing_pages", "duplicate_pages", "status",
            }
        )

    def test_empty_pages_are_detected(self, config):
        document = build_document(page_texts=[BON_TEXTE, "", "", ""])
        report = evaluate(document, config)
        assert report.empty_pages == 3
        assert any(issue.code == "pages_sans_texte" for issue in report.issues)
        assert report.status is not QualityStatus.OK

    def test_missing_pages_are_detected(self, config):
        document = build_document(page_texts=[BON_TEXTE])
        document.analysis.pages = 10  # le PDF en annonçait 10
        report = evaluate(document, config)
        assert report.missing_pages == 9
        assert any(issue.code == "pages_manquantes" for issue in report.issues)
        assert report.status is QualityStatus.FAILED

    def test_duplicate_pages_are_detected(self, config):
        page = "Article 1er : " + "disposition repetee. " * 15
        document = build_document(page_texts=[page, page])
        report = evaluate(document, config)
        assert report.duplicate_pages == 1
        assert any(issue.code == "pages_dupliquees" for issue in report.issues)

    def test_numbering_gap_is_reported(self, config):
        document = build_document(articles=3)
        document.articles[2].article_number = "9"
        document.articles[2].numeric_value = 9.0
        report = evaluate(document, config)
        assert report.numbering_gaps
        assert any(issue.code == "numerotation_incoherente" for issue in report.issues)

    def test_no_articles_is_flagged(self, config):
        document = build_document(articles=0)
        report = evaluate(document, config)
        assert report.articles_detected == 0
        assert any(issue.code == "aucun_article" for issue in report.issues)
        assert report.structure_quality == 0.0

    def test_incomplete_article_is_flagged(self, config):
        document = build_document()
        document.articles[0].text = "Abroge."
        document.articles[0].warnings.append("article_potentiellement_incomplet")
        report = evaluate(document, config)
        assert any(issue.code == "article_incomplet" for issue in report.issues)

    def test_suspect_characters_are_flagged(self, config):
        document = build_document(page_texts=["Art�cle 1�r : t�xte d�grad� " * 5])
        report = evaluate(document, config)
        assert any(issue.code == "caracteres_anormaux" for issue in report.issues)

    def test_missing_ocr_caps_the_score(self, config):
        """Un document scanné extrait sans OCR ne peut pas être « ok »."""
        document = build_document(ocr_required=True, method=ExtractionMethod.NATIVE)
        report = evaluate(document, config)
        assert report.score <= 0.40
        assert report.status is QualityStatus.FAILED
        assert any(issue.code == "ocr_non_applique" for issue in report.issues)

    def test_ocr_confidence_is_reported(self, config):
        document = build_document(method=ExtractionMethod.OCR, ocr_confidence=0.94)
        report = evaluate(document, config)
        assert report.ocr_quality == pytest.approx(0.94)

    def test_poor_metadata_lowers_the_score(self, config):
        rich = evaluate(build_document("a", with_metadata=True), config)
        poor = evaluate(build_document("b", with_metadata=False), config)
        assert poor.score < rich.score
        assert any(issue.code == "metadonnees_incompletes" for issue in poor.issues)

    def test_duplicates_appear_as_issues(self, config):
        a = build_document("a", file_hash="h" * 64)
        b = build_document("b", file_hash="h" * 64)
        find_duplicates([a, b], config)
        report = evaluate(b, config)
        assert any(issue.code.startswith("doublon_") for issue in report.issues)

    def test_no_pages_at_all(self, config):
        document = build_document(page_texts=[])
        report = evaluate(document, config)
        assert report.status is QualityStatus.FAILED
        assert any(issue.code == "aucune_page" for issue in report.issues)

    def test_score_stays_within_bounds(self, config):
        for document in (
            build_document(),
            build_document(page_texts=[]),
            build_document(articles=0, with_metadata=False),
            build_document(ocr_required=True),
        ):
            assert 0.0 <= evaluate(document, config).score <= 1.0


class TestThresholds:
    def test_thresholds_come_from_config(self, config):
        document = build_document()
        strict = config.with_overrides({"quality": {"minimum_score": 0.999}})
        assert evaluate(document, config).status is QualityStatus.OK
        assert evaluate(document, strict).status is QualityStatus.REVIEW_REQUIRED

    def test_reject_threshold(self, config):
        document = build_document(page_texts=["", "", ""], articles=0, with_metadata=False)
        assert evaluate(document, config).status is QualityStatus.FAILED


# ---------------------------------------------------------------------------
# Validation humaine (§16)
# ---------------------------------------------------------------------------


class TestHumanValidation:
    def test_the_system_never_validates_itself(self, config):
        """§16 : le système ne doit jamais prétendre que son extraction est parfaite."""
        report = evaluate(build_document(), config)
        assert report.status is QualityStatus.OK
        assert suggest_validation(report) is ValidationStatus.PENDING
        assert suggest_validation(report) is not ValidationStatus.VALIDATED

    def test_problematic_documents_are_routed_to_review(self, config):
        report = evaluate(build_document(page_texts=["", ""], articles=0), config)
        assert suggest_validation(report) is ValidationStatus.TO_REVIEW

    def test_review_queue_is_ordered_by_severity(self, config):
        good = build_document("bon")
        middling = build_document("moyen", page_texts=[BON_TEXTE, ""])
        bad = build_document("mauvais", page_texts=["", ""], articles=0, with_metadata=False)
        evaluate_all([good, middling, bad], config)
        queue = review_queue([good, middling, bad])
        assert queue[0].document_id == "mauvais"
        assert "bon" not in [d.document_id for d in queue]

    def test_comparison_view_shows_the_three_layers(self, config):
        """§16 : document original ↔ texte extrait ↔ article structuré."""
        document = build_document()
        view = comparison_view(document)
        entry = view["articles"][0]
        assert entry["page_raw_text"] and "EN-TETE" in entry["page_raw_text"]
        assert entry["page_cleaned_text"] and "EN-TETE" not in entry["page_cleaned_text"]
        assert entry["article_text"]
        assert view["source_path"].endswith(".pdf")

    def test_comparison_view_for_a_single_article(self):
        document = build_document()
        view = comparison_view(document, "doc_article_2")
        assert len(view["articles"]) == 1
        assert view["articles"][0]["article_number"] == "2"

    def test_three_decisions_of_the_spec_exist(self):
        assert {ValidationStatus.VALIDATED, ValidationStatus.TO_REVIEW, ValidationStatus.REJECTED}


class TestSummary:
    def test_summary_of_a_batch(self, config):
        documents = [build_document("a"), build_document("b", page_texts=["", ""], articles=0)]
        reports = evaluate_all(documents, config)
        summary = summarize(reports)
        assert summary["count"] == 2
        assert summary["min_score"] <= summary["average_score"] <= summary["max_score"]
        assert summary["by_status"]
        assert summary["top_issues"]

    def test_empty_summary(self):
        assert summarize([])["count"] == 0
