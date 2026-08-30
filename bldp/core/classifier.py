"""Module 2 — analyse du PDF et décision d'OCR (§7 du cahier des charges).

Pour chaque document, on mesure ce qui est *observable* — nombre de pages,
volume de texte par page, présence d'images, qualité apparente des caractères —
puis on en déduit une décision motivée :

* le PDF contient assez de texte exploitable → extraction native (PyMuPDF) ;
* sinon → OCR.

La décision n'est jamais un simple booléen : elle est accompagnée d'un score de
confiance et de la liste des raisons qui l'ont produite, afin qu'un humain
puisse la contester (§33). Quand la confiance est basse, le pipeline le signale
plutôt que de trancher silencieusement.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Optional

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import PageAnalysis, PdfAnalysis
from bldp.core.extraction.pymupdf_extractor import ExtractionError, open_pdf

logger = get_logger("classifier")

#: Caractères de remplacement typiques d'un décodage de police raté.
_BROKEN_CHARS = {"�", "\x00"}


def _alpha_ratio(text: str) -> float:
    """Part de caractères alphabétiques parmi les caractères non blancs.

    Un texte natif français tourne autour de 0,75-0,85. Un texte issu d'un
    mauvais OCR ou d'une police mal encodée s'effondre sous 0,5, car il se
    remplit de symboles, chiffres isolés et caractères de remplacement.
    """
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return 0.0
    alpha = sum(1 for c in visible if c.isalpha())
    return alpha / len(visible)


def _broken_char_ratio(text: str) -> float:
    """Part de caractères manifestement non décodés (U+FFFD, NUL, contrôles)."""
    if not text:
        return 0.0
    broken = sum(
        1
        for c in text
        if c in _BROKEN_CHARS
        or (unicodedata.category(c) == "Cc" and c not in "\t\n\r\f\v")
    )
    return broken / len(text)


def analyze_page(page: Any, index: int) -> PageAnalysis:
    """Mesure une page ouverte par PyMuPDF."""
    try:
        text = page.get_text("text") or ""
    except Exception as exc:  # page abîmée : mesurée comme vide, jamais fatale
        logger.warning("Page %d illisible pendant l'analyse : %s", index + 1, exc)
        text = ""
    try:
        images = len(page.get_images(full=True))
    except Exception:
        images = 0
    rect = getattr(page, "rect", None)
    return PageAnalysis(
        page=index + 1,
        char_count=len(text.strip()),
        image_count=images,
        has_text=bool(text.strip()),
        alpha_ratio=round(_alpha_ratio(text), 4),
        width=round(float(getattr(rect, "width", 0.0)), 1),
        height=round(float(getattr(rect, "height", 0.0)), 1),
    )


def _sample_indices(page_count: int, sample: int) -> list[int]:
    """Indices de pages à analyser, répartis uniformément.

    ``sample <= 0`` ou un document plus court que l'échantillon → toutes les
    pages. L'échantillonnage sert uniquement aux très gros documents, sur une
    machine à 16 Go de RAM.
    """
    if sample <= 0 or page_count <= sample:
        return list(range(page_count))
    step = page_count / sample
    return sorted({int(i * step) for i in range(sample)})


def analyze_pdf(
    path: str | Path,
    document_id: str,
    config: Config,
) -> PdfAnalysis:
    """Analyse un PDF et décide si un OCR est nécessaire.

    Args:
        path: chemin du PDF.
        document_id: identifiant du document.
        config: configuration (section ``classifier``).

    Returns:
        Le diagnostic complet, avec ``ocr_required``, ``confidence`` et
        ``reasons``.

    Raises:
        ExtractionError: le PDF est illisible (l'appelant décide quoi en faire).
    """
    min_chars = int(config.get("classifier.min_chars_per_page", 100))
    min_ratio = float(config.get("classifier.min_text_page_ratio", 0.60))
    min_alpha = float(config.get("classifier.min_alpha_ratio", 0.55))
    sample = int(config.get("classifier.sample_pages", 0))

    target = Path(path)
    size_bytes = target.stat().st_size if target.exists() else 0
    document = open_pdf(target)
    try:
        page_count = document.page_count
        indices = _sample_indices(page_count, sample)
        pages_detail = [analyze_page(document.load_page(i), i) for i in indices]
        encrypted = bool(getattr(document, "is_encrypted", False))
    finally:
        document.close()

    return _decide(
        document_id=document_id,
        page_count=page_count,
        size_bytes=size_bytes,
        pages_detail=pages_detail,
        encrypted=encrypted,
        min_chars=min_chars,
        min_ratio=min_ratio,
        min_alpha=min_alpha,
        sampled=len(pages_detail) < page_count,
    )


def _decide(
    document_id: str,
    page_count: int,
    size_bytes: int,
    pages_detail: list[PageAnalysis],
    encrypted: bool,
    min_chars: int,
    min_ratio: float,
    min_alpha: float,
    sampled: bool,
) -> PdfAnalysis:
    """Applique les règles de décision sur les mesures collectées.

    Isolé de l'ouverture du PDF pour rester testable sans fichier réel.
    """
    reasons: list[str] = []
    analyzed = len(pages_detail)

    if page_count == 0 or analyzed == 0:
        return PdfAnalysis(
            document_id=document_id,
            pages=page_count,
            size_bytes=size_bytes,
            has_text=False,
            ocr_required=False,
            confidence=0.0,
            encrypted=encrypted,
            reasons=["document vide : aucune page analysable"],
        )

    total_chars = sum(p.char_count for p in pages_detail)
    total_images = sum(p.image_count for p in pages_detail)
    rich_pages = [p for p in pages_detail if p.char_count >= min_chars]
    text_pages = [p for p in pages_detail if p.has_text]
    text_page_ratio = len(rich_pages) / analyzed
    mean_chars = total_chars / analyzed

    # Qualité apparente des caractères, mesurée sur les pages qui en ont.
    alpha_values = [p.alpha_ratio for p in text_pages if p.char_count >= 20]
    mean_alpha = sum(alpha_values) / len(alpha_values) if alpha_values else 0.0

    has_text = total_chars > 0
    ocr_required = False

    if total_chars == 0:
        ocr_required = True
        reasons.append("aucun texte natif : document très probablement scanné")
        if total_images:
            reasons.append(f"{total_images} image(s) détectée(s)")
    elif text_page_ratio < min_ratio:
        ocr_required = True
        reasons.append(
            f"seules {text_page_ratio:.0%} des pages atteignent {min_chars} caractères "
            f"(seuil : {min_ratio:.0%})"
        )
    elif mean_alpha and mean_alpha < min_alpha:
        ocr_required = True
        reasons.append(
            f"texte présent mais suspect : {mean_alpha:.0%} de caractères alphabétiques "
            f"(seuil : {min_alpha:.0%}) — police non décodable ou OCR antérieur dégradé"
        )
    else:
        reasons.append(
            f"{text_page_ratio:.0%} des pages contiennent du texte exploitable "
            f"({mean_chars:.0f} caractères/page en moyenne)"
        )

    # Pages isolées à re-OCRiser dans un document par ailleurs textuel.
    pages_needing_ocr = (
        [] if ocr_required else [p.page for p in pages_detail if p.char_count < min_chars]
    )
    if pages_needing_ocr:
        reasons.append(
            f"{len(pages_needing_ocr)} page(s) pauvres en texte au sein d'un document "
            "globalement lisible : OCR ciblé recommandé"
        )

    confidence = _confidence(
        ocr_required=ocr_required,
        text_page_ratio=text_page_ratio,
        mean_alpha=mean_alpha,
        min_ratio=min_ratio,
        min_alpha=min_alpha,
        total_chars=total_chars,
    )

    if sampled:
        reasons.append(f"décision fondée sur un échantillon de {analyzed}/{page_count} pages")
        confidence = round(confidence * 0.95, 4)
    if encrypted:
        reasons.append("document chiffré : extraction éventuellement partielle")

    if confidence < 0.70:
        reasons.append("confiance faible : vérification humaine recommandée")

    analysis = PdfAnalysis(
        document_id=document_id,
        pages=page_count,
        size_bytes=size_bytes,
        has_text=has_text,
        ocr_required=ocr_required,
        confidence=confidence,
        text_page_ratio=round(text_page_ratio, 4),
        total_chars=total_chars,
        total_images=total_images,
        mean_chars_per_page=round(mean_chars, 1),
        encrypted=encrypted,
        reasons=reasons,
        pages_detail=pages_detail,
        pages_needing_ocr=pages_needing_ocr,
    )

    logger.info(
        "%s : %d page(s) détectée(s), texte=%s, OCR %s (confiance %.2f)",
        document_id,
        page_count,
        "oui" if has_text else "non",
        "nécessaire" if ocr_required else "non nécessaire",
        confidence,
    )
    return analysis


def _confidence(
    ocr_required: bool,
    text_page_ratio: float,
    mean_alpha: float,
    min_ratio: float,
    min_alpha: float,
    total_chars: int,
) -> float:
    """Score de confiance dans la décision, entre 0 et 1.

    La confiance est maximale loin des seuils et s'effondre à leur voisinage :
    c'est précisément là qu'un humain doit trancher.
    """
    if total_chars == 0:
        return 0.98  # absence totale de texte : diagnostic quasi certain

    # Distance relative au seuil déterminant.
    ratio_margin = abs(text_page_ratio - min_ratio) / max(min_ratio, 1e-6)
    alpha_margin = (
        abs(mean_alpha - min_alpha) / max(min_alpha, 1e-6) if mean_alpha else 1.0
    )
    margin = min(1.0, min(ratio_margin, alpha_margin))

    base = 0.60 + 0.38 * margin
    if not ocr_required and text_page_ratio >= 0.95 and mean_alpha >= 0.70:
        base = max(base, 0.95)
    return round(min(base, 0.99), 4)


def decide_extraction_route(analysis: PdfAnalysis, config: Config) -> str:
    """Traduit l'analyse en itinéraire d'extraction concret.

    Returns:
        ``"native"``, ``"ocr"`` ou ``"hybrid"`` (natif + OCR sur certaines pages).
        Si l'OCR est désactivé en configuration, on retombe sur ``"native"`` :
        mieux vaut un texte partiel signalé qu'un document perdu.
    """
    ocr_enabled = bool(config.get("ocr.enabled", True))

    if analysis.ocr_required:
        if not ocr_enabled:
            logger.warning(
                "%s nécessite un OCR mais l'OCR est désactivé : extraction native partielle.",
                analysis.document_id,
            )
            return "native"
        return "ocr"

    if analysis.pages_needing_ocr and ocr_enabled:
        return "hybrid"
    return "native"


def analyze_or_none(
    path: str | Path, document_id: str, config: Config
) -> Optional[PdfAnalysis]:
    """Variante tolérante : renvoie ``None`` au lieu de lever (§26).

    Utilisée par le pipeline de masse, où un document illisible ne doit pas
    interrompre le traitement des autres.
    """
    try:
        return analyze_pdf(path, document_id, config)
    except ExtractionError as exc:
        logger.error("Analyse impossible pour %s : %s", document_id, exc)
        return None
