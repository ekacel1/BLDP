"""Module 10 — contrôle qualité (§15 du cahier des charges).

Chaque document reçoit un rapport chiffré et une liste d'anomalies nommées.
Le score global agrège trois dimensions :

``text_quality``
    le texte extrait ressemble-t-il à du français lisible ? (pages vides,
    caractères anormaux, mots hachés, densité) ;
``structure_quality``
    la structure juridique tient-elle debout ? (articles détectés, continuité
    de la numérotation, contexte hiérarchique) ;
``metadata`` (via le module 7)
    les métadonnées minimales sont-elles renseignées ?

Le principe du §33 gouverne l'interprétation : **un score n'est pas une
autorisation**. Un document au-dessus du seuil est simplement *présumé* propre ;
tout ce qui est douteux est marqué ``review_required`` et attend un humain. Le
système ne prétend jamais que son extraction est parfaite (§16).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import (
    Document,
    DocumentType,
    ExtractionMethod,
    Page,
    QualityIssue,
    QualityReport,
    QualityStatus,
    ValidationStatus,
)
from bldp.core.dedup import find_duplicate_pages
from bldp.core.metadata.engine import iter_missing_fields, metadata_completeness
from bldp.core.parser.legal_parser import check_numbering

logger = get_logger("quality")

#: Caractères jamais attendus dans un texte juridique français correct.
SUSPECT_CHARS = set("�□■◆●¤·§¶†‡")

#: Séquence typique d'un OCR défaillant : lettres isolées à la chaîne.
FRAGMENTED_WORDS_RE = re.compile(r"(?:\b\w\b[ ]){4,}")

#: Mots français fréquents, utilisés comme témoin de lisibilité.
COMMON_WORDS = (
    " le ", " la ", " les ", " de ", " des ", " du ", " et ", " est ", " en ",
    " un ", " une ", " par ", " pour ", " dans ", " qui ", " que ", " sur ",
)


# ---------------------------------------------------------------------------
# Mesures élémentaires
# ---------------------------------------------------------------------------


def suspect_char_ratio(text: str) -> float:
    """Part de caractères manifestement anormaux."""
    if not text:
        return 0.0
    suspect = sum(
        1
        for char in text
        if char in SUSPECT_CHARS
        or (unicodedata.category(char) == "Cc" and char not in "\t\n\r")
    )
    return suspect / len(text)


def readability_score(text: str) -> float:
    """Estime la lisibilité du texte, entre 0 et 1.

    Combine trois signaux simples mais robustes : proportion de lettres,
    présence de mots-outils français, et absence de fragmentation en lettres
    isolées (symptôme d'un OCR raté).
    """
    if not text.strip():
        return 0.0

    visible = [c for c in text if not c.isspace()]
    alpha_ratio = sum(1 for c in visible if c.isalpha()) / len(visible) if visible else 0.0

    lowered = f" {text.lower()} "
    hits = sum(1 for word in COMMON_WORDS if word in lowered)
    lexical = min(1.0, hits / 8)

    fragments = len(FRAGMENTED_WORDS_RE.findall(text))
    words = max(1, len(text.split()))
    fragmentation = min(1.0, fragments * 20 / words)

    return round(max(0.0, 0.5 * alpha_ratio + 0.4 * lexical - 0.3 * fragmentation + 0.1), 4)


def page_quality(page: Page) -> float:
    """Qualité d'une page isolée."""
    if not page.text.strip():
        return 0.0
    score = readability_score(page.text)
    score -= min(0.4, suspect_char_ratio(page.text) * 10)
    if page.ocr_confidence is not None:
        # La confiance OCR est un signal direct : on la mêle à parts égales.
        score = 0.5 * score + 0.5 * page.ocr_confidence
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------


def check_pages(document: Document, config: Config) -> tuple[float, list[QualityIssue], dict]:
    """Contrôles portant sur les pages extraites."""
    issues: list[QualityIssue] = []
    pages = document.pages
    if not pages:
        return 0.0, [
            QualityIssue(
                code="aucune_page",
                severity="error",
                message="aucune page extraite — document illisible ou vide",
            )
        ], {"empty_pages": 0, "duplicate_pages": 0, "missing_pages": 0}

    max_empty_ratio = float(config.get("quality.max_empty_page_ratio", 0.10))
    max_suspect = float(config.get("quality.max_suspect_char_ratio", 0.02))

    empty = [page.page for page in pages if not page.text.strip()]
    if empty:
        ratio = len(empty) / len(pages)
        severity = "error" if ratio > max_empty_ratio else "warning"
        issues.append(
            QualityIssue(
                code="pages_sans_texte",
                severity=severity,
                message=(
                    f"{len(empty)} page(s) sans texte ({ratio:.0%}) : "
                    + ", ".join(str(p) for p in empty[:15])
                    + ("…" if len(empty) > 15 else "")
                ),
                count=len(empty),
            )
        )

    full_text = document.full_text
    suspect = suspect_char_ratio(full_text)
    if suspect > max_suspect:
        issues.append(
            QualityIssue(
                code="caracteres_anormaux",
                severity="warning",
                message=(
                    f"{suspect:.2%} de caractères anormaux (seuil {max_suspect:.2%}) — "
                    "OCR dégradé ou police non décodée"
                ),
            )
        )

    duplicate_pages = find_duplicate_pages(document)
    if duplicate_pages:
        issues.append(
            QualityIssue(
                code="pages_dupliquees",
                severity="warning",
                message=(
                    f"{len(duplicate_pages)} page(s) au contenu identique à une "
                    "précédente : " + ", ".join(str(p) for p in duplicate_pages[:10])
                ),
                count=len(duplicate_pages),
            )
        )

    # Pages annoncées par le PDF mais absentes de l'extraction.
    missing = 0
    if document.analysis and document.analysis.pages:
        missing = max(0, document.analysis.pages - len(pages))
        if missing:
            issues.append(
                QualityIssue(
                    code="pages_manquantes",
                    severity="error",
                    message=(
                        f"{missing} page(s) annoncée(s) par le PDF n'ont pas été "
                        "extraites — document incomplet"
                    ),
                    count=missing,
                )
            )

    # Fragmentation : symptôme d'un OCR qui hache les mots.
    for page in pages:
        if page.text.strip() and FRAGMENTED_WORDS_RE.search(page.text):
            issues.append(
                QualityIssue(
                    code="texte_ocr_suspect",
                    severity="warning",
                    message="mots fragmentés en lettres isolées — OCR probablement dégradé",
                    page=page.page,
                )
            )
            break

    scores = [page_quality(page) for page in pages]
    text_quality = round(sum(scores) / len(scores), 4) if scores else 0.0

    return text_quality, issues, {
        "empty_pages": len(empty),
        "duplicate_pages": len(duplicate_pages),
        "missing_pages": missing,
    }


def check_structure(document: Document, config: Config) -> tuple[float, list[QualityIssue], list[str]]:
    """Contrôles portant sur la structure juridique détectée."""
    issues: list[QualityIssue] = []
    articles = document.articles

    if not articles:
        issues.append(
            QualityIssue(
                code="aucun_article",
                severity="warning",
                message=(
                    "aucun article détecté — texte non normatif, ou format non "
                    "couvert par les règles de parsing"
                ),
            )
        )
        return 0.0, issues, []

    gaps: list[str] = []
    if config.get("parser.check_numbering", True):
        gaps = check_numbering(articles)
        for gap in gaps:
            issues.append(
                QualityIssue(code="numerotation_incoherente", severity="warning", message=gap)
            )

    incomplete = [a for a in articles if "article_potentiellement_incomplet" in a.warnings]
    for article in incomplete[:20]:
        issues.append(
            QualityIssue(
                code="article_incomplet",
                severity="warning",
                message=f"article {article.article_number} potentiellement incomplet "
                        f"({len(article.text)} caractères)",
                article_id=article.article_id,
                page=article.page_start,
            )
        )

    merged = [a for a in articles if "article_anormalement_long_fusion_probable" in a.warnings]
    for article in merged[:10]:
        issues.append(
            QualityIssue(
                code="article_anormalement_long",
                severity="warning",
                message=f"article {article.article_number} anormalement long "
                        "— fusion probable avec le suivant",
                article_id=article.article_id,
                page=article.page_start,
            )
        )

    unreadable_numbers = [a for a in articles if a.numeric_value is None]
    if unreadable_numbers:
        issues.append(
            QualityIssue(
                code="numero_article_illisible",
                severity="warning",
                message=(
                    f"{len(unreadable_numbers)} article(s) au numéro non interprétable : "
                    + ", ".join(a.article_number for a in unreadable_numbers[:8])
                ),
                count=len(unreadable_numbers),
            )
        )

    orphans = [a for a in articles if not a.hierarchy_path]
    orphan_ratio = len(orphans) / len(articles)

    # Composition du score de structure.
    score = 1.0
    score -= min(0.35, len(gaps) * 0.08)
    score -= min(0.20, len(incomplete) / len(articles) * 0.5)
    score -= min(0.15, len(merged) / len(articles))
    score -= min(0.15, len(unreadable_numbers) / len(articles) * 0.5)
    # Une absence totale de hiérarchie est normale pour un décret court :
    # on ne pénalise que modérément.
    score -= min(0.15, orphan_ratio * 0.15)

    return round(max(0.0, score), 4), issues, gaps


def check_metadata(document: Document) -> tuple[float, list[QualityIssue]]:
    """Contrôle la complétude des métadonnées minimales (§12)."""
    issues: list[QualityIssue] = []
    completeness = metadata_completeness(document.metadata)
    missing = list(iter_missing_fields(document.metadata))

    if missing:
        issues.append(
            QualityIssue(
                code="metadonnees_incompletes",
                severity="info" if completeness >= 0.7 else "warning",
                message="métadonnées absentes : " + ", ".join(missing),
                count=len(missing),
            )
        )

    weak = [field for field, score in document.metadata.confidence.items() if score < 0.5]
    if weak:
        issues.append(
            QualityIssue(
                code="metadonnees_peu_fiables",
                severity="info",
                message="champ(s) déduits avec une faible confiance : " + ", ".join(sorted(weak)),
                count=len(weak),
            )
        )
    return completeness, issues


def check_duplicates(document: Document) -> list[QualityIssue]:
    """Remonte les liens de duplication comme anomalies à examiner (§14)."""
    return [
        QualityIssue(
            code=f"doublon_{link.kind}",
            severity="warning" if link.kind in {"file_hash", "text_hash"} else "info",
            message=(
                f"doublon de {link.duplicate_of} ({link.kind}, "
                f"similarité {link.similarity:.0%}) : {link.details}"
            ),
        )
        for link in document.duplicates
    ]


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------


def evaluate(document: Document, config: Config) -> QualityReport:
    """Produit le rapport qualité d'un document (§15).

    Le statut renvoyé n'est jamais un feu vert définitif : il oriente la revue
    humaine. ``ok`` signifie « rien de suspect détecté », pas « exact ».
    """
    review_threshold = float(config.get("quality.review_threshold", 0.75))
    reject_threshold = float(config.get("quality.reject_threshold", 0.50))
    minimum = float(config.get("quality.minimum_score", 0.90))

    text_quality, page_issues, counters = check_pages(document, config)
    structure_quality, structure_issues, gaps = check_structure(document, config)
    metadata_score, metadata_issues = check_metadata(document)
    duplicate_issues = check_duplicates(document)

    issues = [*page_issues, *structure_issues, *metadata_issues, *duplicate_issues]

    ocr_scores = [p.ocr_confidence for p in document.pages if p.ocr_confidence is not None]
    ocr_quality = round(sum(ocr_scores) / len(ocr_scores), 4) if ocr_scores else None

    # Pondération : le texte pèse le plus lourd — sans texte fidèle, la
    # structure et les métadonnées ne valent rien.
    score = 0.50 * text_quality + 0.35 * structure_quality + 0.15 * metadata_score

    # Une erreur bloquante plafonne le score, quel que soit le reste.
    if any(issue.severity == "error" for issue in issues):
        score = min(score, 0.49)

    # Un document extrait sans OCR alors qu'il en fallait un est suspect.
    if document.analysis and document.analysis.ocr_required and document.extraction:
        if document.extraction.method is ExtractionMethod.NATIVE:
            issues.append(
                QualityIssue(
                    code="ocr_non_applique",
                    severity="error",
                    message=(
                        "un OCR était nécessaire mais n'a pas été appliqué — "
                        "texte probablement incomplet"
                    ),
                )
            )
            score = min(score, 0.40)

    score = round(max(0.0, min(1.0, score)), 4)

    if score < reject_threshold:
        status = QualityStatus.FAILED
    elif score < review_threshold or any(i.severity == "error" for i in issues):
        status = QualityStatus.REVIEW_REQUIRED
    elif score < minimum:
        status = QualityStatus.REVIEW_REQUIRED
    else:
        status = QualityStatus.OK

    report = QualityReport(
        document_id=document.document_id,
        score=score,
        ocr_quality=ocr_quality,
        text_quality=text_quality,
        structure_quality=structure_quality,
        pages=len(document.pages),
        empty_pages=counters["empty_pages"],
        duplicate_pages=counters["duplicate_pages"],
        missing_pages=counters["missing_pages"],
        articles_detected=len(document.articles),
        numbering_gaps=gaps,
        possible_errors=sum(1 for i in issues if i.severity in {"warning", "error"}),
        status=status,
        issues=issues,
    )

    logger.info(
        "%s : score %.2f (texte %.2f, structure %.2f, métadonnées %.2f) → %s",
        document.document_id,
        score,
        text_quality,
        structure_quality,
        metadata_score,
        status.value,
    )
    for issue in issues:
        if issue.severity == "error":
            logger.error("%s : %s", document.document_id, issue.message)
        elif issue.severity == "warning":
            logger.warning("%s : %s", document.document_id, issue.message)

    return report


def evaluate_all(documents: Sequence[Document], config: Config) -> list[QualityReport]:
    """Évalue un lot ; attache le rapport à chaque document."""
    reports: list[QualityReport] = []
    for document in documents:
        report = evaluate(document, config)
        document.quality = report
        reports.append(report)
    return reports


# ---------------------------------------------------------------------------
# Aiguillage vers la validation humaine (§16)
# ---------------------------------------------------------------------------


def suggest_validation(report: QualityReport) -> ValidationStatus:
    """Propose une décision de validation — jamais définitive.

    Même un document sans anomalie reste ``en_attente`` : le système ne
    s'auto-valide pas. Il ne fait que trier ce qui doit être regardé en
    priorité.
    """
    if report.status is QualityStatus.FAILED:
        return ValidationStatus.TO_REVIEW
    if report.status is QualityStatus.REVIEW_REQUIRED:
        return ValidationStatus.TO_REVIEW
    return ValidationStatus.PENDING


def review_queue(documents: Sequence[Document]) -> list[Document]:
    """Documents à examiner en priorité, du plus problématique au moins."""
    pending = [
        document
        for document in documents
        if document.quality and document.quality.status is not QualityStatus.OK
    ]
    return sorted(pending, key=lambda d: d.quality.score)


def summarize(reports: Iterable[QualityReport]) -> dict:
    """Synthèse chiffrée d'un lot de rapports."""
    reports = list(reports)
    if not reports:
        return {"count": 0, "average_score": None, "by_status": {}, "top_issues": {}}

    by_status: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for report in reports:
        by_status[report.status.value] = by_status.get(report.status.value, 0) + 1
        for issue in report.issues:
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1

    scores = [report.score for report in reports]
    return {
        "count": len(reports),
        "average_score": round(sum(scores) / len(scores), 4),
        "min_score": min(scores),
        "max_score": max(scores),
        "by_status": by_status,
        "top_issues": dict(
            sorted(issue_counts.items(), key=lambda item: item[1], reverse=True)[:10]
        ),
    }


def comparison_view(document: Document, article_id: str | None = None) -> dict:
    """Vue de vérification humaine : original ↔ texte extrait ↔ article (§16).

    Renvoie de quoi afficher côte à côte, pour un article donné, le texte de la
    page d'origine et l'article structuré qui en a été tiré.
    """
    articles = document.articles
    if article_id:
        articles = [a for a in articles if a.article_id == article_id]
    if not articles:
        return {"document_id": document.document_id, "articles": []}

    pages_by_number = {page.page: page for page in document.pages}
    entries = []
    for article in articles:
        page = pages_by_number.get(article.page_start)
        entries.append(
            {
                "article_id": article.article_id,
                "article_number": article.article_number,
                "hierarchy_path": article.hierarchy_path,
                "page": article.page_start,
                "source_file": article.source_file,
                # Ce que contenait la page avant nettoyage…
                "page_raw_text": page.raw_text if page else None,
                # …après nettoyage…
                "page_cleaned_text": page.text if page else None,
                # …et ce que le parser en a tiré.
                "article_text": article.text,
                "alineas": [alinea.to_dict() for alinea in article.alineas],
                "warnings": article.warnings,
            }
        )
    return {
        "document_id": document.document_id,
        "source_path": document.source.source_path,
        "validation": document.validation.value,
        "articles": entries,
    }
