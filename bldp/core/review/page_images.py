"""Rendu des pages du PDF d'origine, pour que la relecture ait une référence.

C'est la pièce qui rend la relecture honnête, et elle mérite d'être expliquée.

Sans image, un modèle qui « relit » un document compare le texte des articles
au texte des pages — or les deux sortent de la **même** extraction. Là où
l'OCR s'est trompé, il s'est trompé aux deux endroits : il n'y a rien à
comparer. Tout ce que le modèle proposerait alors serait déduit de ce qui
*paraît* cohérent, pas lu. C'est exactement ce qu'on ne veut pas d'un corpus
juridique.

L'image de la page est la seule référence qui ne vienne pas de l'OCR. Avec
elle, la relecture change de nature : ce n'est plus une correction, c'est une
**collation** — on met l'original en regard de la transcription, et on relève
les écarts.

Le rendu vise donc la lisibilité avant la légèreté : une page dont on ne peut
pas lire le texte ne sert à rien, et il vaut mieux écarter un document que le
relire sur une image illisible.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from bldp.logging_setup import get_logger

logger = get_logger("review.images")


class PageImageError(RuntimeError):
    """Le PDF d'origine n'est pas exploitable comme référence."""


#: Côté le plus long du rendu, en pixels.
#:
#: 1568 est la limite au-delà de laquelle l'API redimensionne elle-même :
#: rendre plus large coûte des octets sans rien apporter. En dessous, le texte
#: d'un scan devient trop petit pour être lu de façon fiable.
DEFAULT_MAX_EDGE = 1568

#: Qualité JPEG. Un scan compressé à 85 reste lisible pour un tiers du poids
#: d'un PNG ; c'est le bon compromis pour du texte noir sur blanc.
DEFAULT_JPEG_QUALITY = 85

#: Poids maximal d'une image, en octets, avant encodage base64.
MAX_IMAGE_BYTES = 4_500_000

#: Nombre d'images acceptées dans un seul appel.
MAX_IMAGES_PER_CALL = 100


@dataclass
class PageImage:
    """Une page rendue, prête à être envoyée."""

    page: int
    data: bytes
    media_type: str = "image/jpeg"
    width: int = 0
    height: int = 0

    @property
    def estimated_tokens(self) -> int:
        """Coût en jetons d'une image, selon la règle largeur × hauteur / 750."""
        return int(self.width * self.height / 750) if self.width and self.height else 0

    def to_block(self) -> dict:
        """Bloc de contenu au format attendu par l'API."""
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.media_type,
                "data": base64.standard_b64encode(self.data).decode("ascii"),
            },
        }


def source_pdf(document) -> Optional[Path]:
    """Chemin du PDF d'origine, s'il est encore disponible.

    La copie de travail (``data/raw/``) est préférée au chemin d'import : elle
    appartient au pipeline, alors que le dossier d'origine peut avoir été
    déplacé ou vidé depuis.
    """
    for candidat in (document.source.raw_path, document.source.source_path):
        if candidat and Path(candidat).exists():
            return Path(candidat)
    return None


def render_pages(
    pdf_path: str | Path,
    pages: Sequence[int] = (),
    max_edge: int = DEFAULT_MAX_EDGE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> list[PageImage]:
    """Rend les pages demandées du PDF en images lisibles.

    Args:
        pdf_path: le PDF d'origine.
        pages: numéros de page (à partir de 1). Vide = toutes.
        max_edge: côté le plus long du rendu, en pixels.
        jpeg_quality: qualité de compression.

    Raises:
        PageImageError: PDF absent, illisible, ou page introuvable. On préfère
            refuser un document plutôt que de le relire sans référence.
    """
    import pymupdf

    chemin = Path(pdf_path)
    if not chemin.exists():
        raise PageImageError(
            f"PDF d'origine introuvable : {chemin}. Sans l'original, une "
            "relecture ne compare rien — elle devine."
        )

    rendus: list[PageImage] = []
    try:
        document = pymupdf.open(chemin)
    except Exception as exc:  # noqa: BLE001 — pymupdf lève des types variés
        raise PageImageError(f"PDF illisible ({chemin.name}) : {exc}") from exc

    with document:
        voulues = list(pages) or list(range(1, document.page_count + 1))
        for numero in voulues:
            if not 1 <= numero <= document.page_count:
                raise PageImageError(
                    f"page {numero} absente de {chemin.name} "
                    f"({document.page_count} page(s))"
                )
            page = document[numero - 1]
            # Le zoom est calculé pour que le côté le plus long tombe sur
            # ``max_edge`` : c'est la résolution utile, ni plus ni moins.
            cote = max(page.rect.width, page.rect.height) or 1
            zoom = max_edge / cote
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            donnees = pixmap.tobytes("jpeg", jpg_quality=jpeg_quality)

            if len(donnees) > MAX_IMAGE_BYTES:
                # Une page trop lourde est recomprimée plutôt qu'abandonnée :
                # la perte de qualité vaut mieux qu'une page manquante.
                donnees = pixmap.tobytes("jpeg", jpg_quality=60)
                logger.info(
                    "Page %d de %s recomprimée (%d Ko).",
                    numero, chemin.name, len(donnees) // 1024,
                )
            if len(donnees) > MAX_IMAGE_BYTES:
                raise PageImageError(
                    f"page {numero} de {chemin.name} trop lourde même "
                    f"recomprimée ({len(donnees) // 1024} Ko)"
                )

            rendus.append(
                PageImage(
                    page=numero, data=donnees,
                    width=pixmap.width, height=pixmap.height,
                )
            )

    return rendus


def estimate_images(
    document, max_edge: int = DEFAULT_MAX_EDGE, max_pages: int = MAX_IMAGES_PER_CALL
) -> tuple[int, int]:
    """Combien d'images, et combien de jetons, sans rien rendre.

    Sert à chiffrer un lot avant de l'envoyer. On lit seulement les dimensions
    des pages : c'est immédiat, là où rendre cent pages prend plusieurs
    secondes pour un chiffre qu'on peut obtenir autrement.

    Returns:
        ``(nombre d'images, jetons estimés)``.

    Raises:
        PageImageError: même diagnostic que :func:`render_document`, afin
            qu'un document écarté le soit dès le plan et pour la même raison.
    """
    import pymupdf

    chemin = source_pdf(document)
    if chemin is None:
        raise PageImageError(
            f"PDF d'origine introuvable pour {document.document_id} : "
            "la relecture exige l'image de la page, faute de quoi elle "
            "comparerait l'OCR à lui-même."
        )

    numeros = [page.page for page in document.pages]
    if len(numeros) > max_pages:
        raise PageImageError(
            f"{len(numeros)} pages pour une limite de {max_pages} images par "
            "appel. Relire une partie du document et conclure sur l'ensemble "
            "serait un faux diagnostic : découpez-le."
        )

    try:
        pdf = pymupdf.open(chemin)
    except Exception as exc:  # noqa: BLE001
        raise PageImageError(f"PDF illisible ({chemin.name}) : {exc}") from exc

    jetons = 0
    with pdf:
        for numero in numeros:
            if not 1 <= numero <= pdf.page_count:
                raise PageImageError(
                    f"page {numero} absente de {chemin.name} "
                    f"({pdf.page_count} page(s))"
                )
            rect = pdf[numero - 1].rect
            cote = max(rect.width, rect.height) or 1
            zoom = max_edge / cote
            jetons += int((rect.width * zoom) * (rect.height * zoom) / 750)
    return len(numeros), jetons


def render_document(
    document,
    max_edge: int = DEFAULT_MAX_EDGE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    max_pages: int = MAX_IMAGES_PER_CALL,
) -> list[PageImage]:
    """Rend les pages d'un document du corpus.

    Un document plus long que ``max_pages`` est **refusé**, pas tronqué :
    relire les vingt premières pages d'un code et conclure sur l'ensemble
    serait un faux diagnostic, et l'absence des dernières pages ne se verrait
    nulle part dans le résultat.
    """
    chemin = source_pdf(document)
    if chemin is None:
        raise PageImageError(
            f"PDF d'origine introuvable pour {document.document_id} : "
            "la relecture exige l'image de la page, faute de quoi elle "
            "comparerait l'OCR à lui-même."
        )

    numeros = [page.page for page in document.pages]
    if len(numeros) > max_pages:
        raise PageImageError(
            f"{len(numeros)} pages pour une limite de {max_pages} images par "
            "appel. Relire une partie du document et conclure sur l'ensemble "
            "serait un faux diagnostic : découpez-le."
        )
    return render_pages(chemin, numeros, max_edge, jpeg_quality)
