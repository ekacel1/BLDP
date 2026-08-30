"""Module 3 — extraction native du texte avec PyMuPDF (§8).

L'extraction se fait **page par page** et conserve systématiquement le numéro de
page, afin de pouvoir retrouver l'origine exacte de toute information (§33).

Le texte est renvoyé brut : aucun nettoyage n'est appliqué ici. Le module 4
(``bldp.core.cleaning``) est seul responsable des transformations, ce qui permet
de toujours comparer le texte final au texte réellement présent dans le PDF.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator, Optional

from bldp.logging_setup import get_logger
from bldp.models import ExtractionMethod, ExtractionResult, Page

logger = get_logger("extraction.pymupdf")

#: Mode d'extraction PyMuPDF. ``"text"`` respecte l'ordre de lecture naturel et
#: reste robuste sur les documents juridiques à une colonne.
DEFAULT_TEXT_MODE = "text"


class ExtractionError(RuntimeError):
    """Le PDF n'a pas pu être ouvert ou lu."""


def _import_fitz() -> Any:
    """Importe PyMuPDF avec un message d'erreur exploitable si absent."""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError as exc:  # pragma: no cover - dépendance obligatoire
            raise ExtractionError(
                "PyMuPDF est requis pour l'extraction PDF. "
                "Installez-le avec : pip install PyMuPDF"
            ) from exc


def open_pdf(path: str | Path, password: str | None = None) -> Any:
    """Ouvre un PDF et renvoie le document PyMuPDF.

    Raises:
        ExtractionError: fichier absent, corrompu ou chiffré sans mot de passe.
    """
    fitz = _import_fitz()
    target = Path(path)
    if not target.exists():
        raise ExtractionError(f"Fichier introuvable : {target}")
    try:
        document = fitz.open(target)
    except Exception as exc:  # PyMuPDF lève des exceptions variées
        raise ExtractionError(f"PDF illisible ({target.name}) : {exc}") from exc

    if document.needs_pass:
        opened = document.authenticate(password or "")
        if not opened:
            document.close()
            raise ExtractionError(
                f"PDF protégé par mot de passe : {target.name}. "
                "Fournissez le mot de passe ou déprotégez le fichier."
            )
    return document


def iter_page_texts(
    path: str | Path,
    mode: str = DEFAULT_TEXT_MODE,
    pages: Optional[list[int]] = None,
) -> Iterator[tuple[int, str]]:
    """Itère sur ``(numero_de_page_1_based, texte_brut)``.

    Args:
        path: chemin du PDF.
        mode: mode d'extraction PyMuPDF (``text``, ``blocks``, ``words``...).
        pages: numéros de page (1-based) à extraire ; ``None`` = toutes.
    """
    document = open_pdf(path)
    try:
        wanted = set(pages) if pages else None
        for index in range(document.page_count):
            page_number = index + 1
            if wanted is not None and page_number not in wanted:
                continue
            try:
                text = document.load_page(index).get_text(mode) or ""
            except Exception as exc:  # une page abîmée ne stoppe pas le document
                logger.warning("Page %d illisible dans %s : %s", page_number, path, exc)
                text = ""
            yield page_number, text
    finally:
        document.close()


def extract_document(
    path: str | Path,
    document_id: str,
    mode: str = DEFAULT_TEXT_MODE,
    pages: Optional[list[int]] = None,
    source_file: str | None = None,
) -> ExtractionResult:
    """Extrait tout le texte natif d'un PDF.

    Chaque page produit un :class:`~bldp.models.Page` conservant son numéro et
    son fichier d'origine. Le texte est stocké à la fois dans ``text`` et
    ``raw_text`` : le second ne sera jamais modifié par la suite du pipeline.

    Args:
        path: PDF à lire.
        document_id: identifiant du document dans le corpus.
        mode: mode d'extraction PyMuPDF.
        pages: sous-ensemble de pages (1-based) ; ``None`` = tout le document.
        source_file: nom affiché comme source (défaut : nom du fichier).

    Returns:
        Le résultat d'extraction, méthode ``pymupdf``.
    """
    started = time.perf_counter()
    target = Path(path)
    label = source_file or target.name

    result = ExtractionResult(
        document_id=document_id,
        source_file=label,
        method=ExtractionMethod.NATIVE,
    )

    empty_pages: list[int] = []
    for page_number, raw_text in iter_page_texts(target, mode=mode, pages=pages):
        text = raw_text
        page = Page(
            document_id=document_id,
            page=page_number,
            text=text,
            source_file=label,
            raw_text=raw_text,
            method=ExtractionMethod.NATIVE,
        )
        if not text.strip():
            page.warnings.append("page_sans_texte")
            empty_pages.append(page_number)
        result.pages.append(page)

    result.duration_seconds = round(time.perf_counter() - started, 3)

    if empty_pages:
        preview = ", ".join(str(p) for p in empty_pages[:10])
        suffix = "..." if len(empty_pages) > 10 else ""
        result.warnings.append(
            f"{len(empty_pages)} page(s) sans texte natif : {preview}{suffix}"
        )
        logger.warning(
            "%s : %d page(s) sans texte natif (OCR peut-être nécessaire)",
            document_id,
            len(empty_pages),
        )

    logger.info(
        "%s : %d page(s) extraite(s), %d caractères en %.2fs",
        document_id,
        len(result.pages),
        result.total_chars,
        result.duration_seconds,
    )
    return result


def extract_pdf_metadata(path: str | Path) -> dict:
    """Métadonnées du conteneur PDF (titre, auteur, dates, producteur).

    Ces valeurs sont des indices : elles sont souvent absentes ou trompeuses
    dans les documents officiels numérisés. Le module 7 ne les retient qu'avec
    une confiance faible.
    """
    document = open_pdf(path)
    try:
        raw = dict(document.metadata or {})
        return {
            "pdf_title": (raw.get("title") or "").strip() or None,
            "pdf_author": (raw.get("author") or "").strip() or None,
            "pdf_subject": (raw.get("subject") or "").strip() or None,
            "pdf_keywords": (raw.get("keywords") or "").strip() or None,
            "pdf_creator": (raw.get("creator") or "").strip() or None,
            "pdf_producer": (raw.get("producer") or "").strip() or None,
            "pdf_creation_date": (raw.get("creationDate") or "").strip() or None,
            "pdf_modification_date": (raw.get("modDate") or "").strip() or None,
            "page_count": document.page_count,
        }
    finally:
        document.close()


def extract_toc(path: str | Path) -> list[dict]:
    """Table des matières intégrée au PDF, si elle existe.

    Utile comme signal de contrôle pour le parser : un sommaire présent permet
    de vérifier que les titres détectés dans le texte sont cohérents.
    """
    document = open_pdf(path)
    try:
        return [
            {"level": level, "title": title.strip(), "page": page}
            for level, title, page in (document.get_toc() or [])
        ]
    finally:
        document.close()
