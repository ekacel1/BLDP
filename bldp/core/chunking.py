"""Chunking juridiquement conscient (§20 du cahier des charges).

Le découpage suit la hiérarchie du droit, pas une longueur arbitraire :

.. code-block:: text

    Article  →  alinéas  →  paragraphes

Un article qui tient dans la taille cible forme **un seul chunk** : c'est
l'unité de citation naturelle du droit. Un article trop long est découpé sur
ses alinéas, qui sont eux-mêmes des unités juridiques complètes. Ce n'est qu'en
dernier recours — un alinéa unique démesuré — que l'on coupe à l'intérieur, et
alors sur des frontières de phrase, jamais au milieu d'une proposition.

Chaque chunk conserve tout son contexte : document, article, chapitre, section,
page source et position, de sorte qu'un résultat de recherche puisse toujours
être ramené à sa source exacte (§20 et §33).
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import Article, Chunk, Document

logger = get_logger("chunking")

#: Fin de phrase : point, point-virgule, deux-points ou ponctuation forte suivie
#: d'un espace et d'une majuscule ou d'un début d'énumération.
SENTENCE_END_RE = re.compile(r"(?<=[.;:!?])\s+(?=[A-ZÀ-Ü0-9«\"(-])")

#: Marge de tolérance : un article dépassant la cible de moins de 20 % reste
#: entier plutôt que d'être coupé pour quelques mots.
OVERSHOOT_TOLERANCE = 1.20


def _chunk_id(article_id: str | None, document_id: str, index: int) -> str:
    base = article_id or f"{document_id}_texte"
    return f"{base}_chunk_{index:03d}"


def _context_from_article(article: Article) -> dict:
    """Contexte hiérarchique et de provenance d'un article."""
    return {
        "article_id": article.article_id,
        "article_number": article.article_number,
        "page": article.page_start,
        "title": article.title,
        "chapter": article.chapter,
        "section": article.section,
        "hierarchy_path": list(article.hierarchy_path),
    }


def split_sentences(text: str) -> list[str]:
    """Découpe un texte en phrases, sans jamais couper à l'intérieur.

    Découpage volontairement conservateur : mieux vaut une « phrase » un peu
    longue qu'une proposition juridique tronquée.
    """
    parts = [part.strip() for part in SENTENCE_END_RE.split(text) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def pack_units(
    units: Sequence[str],
    max_chars: int,
    overlap_chars: int = 0,
) -> list[str]:
    """Regroupe des unités indivisibles en blocs sous la taille cible.

    Une unité plus longue que ``max_chars`` est conservée telle quelle : elle
    est indivisible par construction (l'appelant a déjà choisi la granularité).
    """
    blocks: list[str] = []
    current: list[str] = []
    length = 0

    for unit in units:
        unit_length = len(unit)
        if current and length + unit_length + 1 > max_chars:
            blocks.append(" ".join(current))
            if overlap_chars > 0:
                tail = _tail(current, overlap_chars)
                current = list(tail)
                length = sum(len(t) + 1 for t in tail)
            else:
                current, length = [], 0
        current.append(unit)
        length += unit_length + 1

    if current:
        blocks.append(" ".join(current))
    return blocks


def _tail(units: Sequence[str], overlap_chars: int) -> list[str]:
    """Dernières unités d'un bloc, dans la limite du recouvrement demandé."""
    tail: list[str] = []
    total = 0
    for unit in reversed(units):
        if total + len(unit) > overlap_chars and tail:
            break
        tail.insert(0, unit)
        total += len(unit) + 1
    return tail


# ---------------------------------------------------------------------------
# Découpage d'un article
# ---------------------------------------------------------------------------


def chunk_article(
    article: Article,
    document: Document,
    max_chars: int,
    overlap_chars: int = 0,
    respect_sentences: bool = True,
    strategy: str = "article",
    start_index: int = 0,
    keep_whole: bool = True,
) -> list[Chunk]:
    """Découpe un article en chunks, du plus intègre au plus fragmenté.

    Ordre de priorité (§20) :

    1. l'article entier, s'il tient (à la tolérance près) ;
    2. regroupements d'alinéas complets ;
    3. regroupements de phrases, si un alinéa unique est trop long.

    Args:
        keep_whole: conserver l'article entier quand il tient dans la taille
            cible. Mis à ``False`` par la stratégie « fixed ».
    """
    text = article.text.strip()
    if not text:
        return []

    context = _context_from_article(article)
    common = {
        "document_id": document.document_id,
        "strategy": strategy,
        "char_start": article.char_start,
        "char_end": article.char_end,
        **context,
    }

    def build(index: int, body: str, part: int, total: int) -> Chunk:
        return Chunk(
            chunk_id=_chunk_id(article.article_id, document.document_id, index),
            text=body,
            position=index,
            metadata={
                "document_title": document.metadata.title,
                "document_type": document.metadata.type.value,
                "document_number": document.metadata.number,
                "document_date": document.metadata.date,
                "document_status": document.metadata.status.value,
                "jurisdiction": document.metadata.jurisdiction,
                "source_file": article.source_file or document.source.filename,
                "page_start": article.page_start,
                "page_end": article.page_end,
                "part": part,
                "part_count": total,
                "article_label": article.label,
            },
            **common,
        )

    # 1. L'article entier tient : c'est le cas idéal.
    if keep_whole and len(text) <= max_chars * OVERSHOOT_TOLERANCE:
        return [build(start_index, text, 1, 1)]

    # 2. Découpage sur les alinéas — unités juridiques complètes.
    units: list[str]
    if article.alineas and len(article.alineas) > 1:
        units = [alinea.text for alinea in article.alineas if alinea.text.strip()]
    else:
        units = [text]

    blocks = pack_units(units, max_chars, overlap_chars)

    # 3. Un bloc reste trop long (alinéa unique démesuré) : on descend aux
    #    phrases, sans jamais couper à l'intérieur de l'une d'elles.
    if respect_sentences:
        refined: list[str] = []
        for block in blocks:
            if len(block) <= max_chars * OVERSHOOT_TOLERANCE:
                refined.append(block)
            else:
                refined.extend(pack_units(split_sentences(block), max_chars, overlap_chars))
        blocks = refined

    total = len(blocks)
    if total > 1:
        logger.debug(
            "Article %s de %s découpé en %d fragments",
            article.article_number,
            document.document_id,
            total,
        )
    return [build(start_index + i, block, i + 1, total) for i, block in enumerate(blocks)]


# ---------------------------------------------------------------------------
# Découpage d'un document
# ---------------------------------------------------------------------------


def chunk_document(document: Document, config: Config) -> list[Chunk]:
    """Découpe un document entier en chunks prêts pour l'embedding.

    Si aucun article n'a été détecté, on retombe sur un découpage par page
    plutôt que de ne rien produire — le texte reste exploitable, et le chunk
    porte la mention de sa stratégie dégradée.
    """
    strategy = str(config.get("chunking.strategy", "article"))
    max_chars = int(config.get("chunking.max_chars", 1500))
    overlap = int(config.get("chunking.overlap_chars", 150))
    respect_sentences = bool(config.get("chunking.respect_sentences", True))

    if overlap >= max_chars:
        logger.warning(
            "chunking.overlap_chars (%d) >= max_chars (%d) : recouvrement ramené à 10%%.",
            overlap,
            max_chars,
        )
        overlap = max_chars // 10

    if strategy not in {"article", "alinea", "fixed"}:
        logger.warning(
            "chunking.strategy=%r inconnue : repli sur « article ».", strategy
        )
        strategy = "article"

    if not document.articles:
        return _chunk_pages(document, max_chars, overlap, respect_sentences)

    chunks: list[Chunk] = []
    for article in document.articles:
        if strategy == "alinea":
            produced = _chunk_by_alinea(article, document, len(chunks))
        else:
            # « fixed » découpe systématiquement à la taille cible ; « article »
            # laisse entier tout article qui tient.
            produced = chunk_article(
                article,
                document,
                max_chars=max_chars,
                overlap_chars=overlap,
                respect_sentences=respect_sentences,
                strategy=strategy,
                start_index=len(chunks),
                keep_whole=(strategy == "article"),
            )
        chunks.extend(produced)

    logger.info("%s : %d fragment(s) produit(s)", document.document_id, len(chunks))
    return chunks


def _chunk_by_alinea(article: Article, document: Document, start_index: int) -> list[Chunk]:
    """Stratégie « alinea » : un chunk par alinéa, pour une granularité fine."""
    alineas = [a for a in article.alineas if a.text.strip()] or None
    if alineas is None:
        return chunk_article(article, document, max_chars=10**9, start_index=start_index)

    context = _context_from_article(article)
    return [
        Chunk(
            chunk_id=f"{article.article_id}_alinea_{alinea.index:03d}",
            document_id=document.document_id,
            text=alinea.text,
            position=start_index + offset,
            strategy="alinea",
            char_start=article.char_start,
            char_end=article.char_end,
            metadata={
                "document_title": document.metadata.title,
                "document_number": document.metadata.number,
                "source_file": article.source_file or document.source.filename,
                "alinea_index": alinea.index,
                "alinea_number": alinea.number,
                "article_label": article.label,
            },
            **context,
        )
        for offset, alinea in enumerate(alineas)
    ]


def _chunk_pages(
    document: Document,
    max_chars: int,
    overlap: int,
    respect_sentences: bool,
) -> list[Chunk]:
    """Repli : découpage par page lorsque aucun article n'a été détecté."""
    chunks: list[Chunk] = []
    for page in document.pages:
        text = page.text.strip()
        if not text:
            continue
        units = split_sentences(text) if respect_sentences else [text]
        for block in pack_units(units, max_chars, overlap):
            chunks.append(
                Chunk(
                    chunk_id=f"{document.document_id}_page_{page.page:04d}_chunk_{len(chunks):03d}",
                    document_id=document.document_id,
                    text=block,
                    position=len(chunks),
                    page=page.page,
                    strategy="page",
                    metadata={
                        "document_title": document.metadata.title,
                        "source_file": page.source_file,
                        "note": "aucun article détecté : découpage par page",
                    },
                )
            )
    if chunks:
        logger.warning(
            "%s : aucun article détecté, %d fragment(s) produit(s) par page",
            document.document_id,
            len(chunks),
        )
    return chunks


def chunk_documents(documents: Sequence[Document], config: Config) -> list[Chunk]:
    """Découpe un lot de documents."""
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunk_document(document, config))
    return chunks


def chunking_stats(chunks: Iterable[Chunk]) -> dict:
    """Statistiques utiles pour régler ``chunking.max_chars``."""
    lengths = [len(chunk.text) for chunk in chunks]
    if not lengths:
        return {"count": 0}
    by_strategy: dict[str, int] = {}
    for chunk in chunks:
        by_strategy[chunk.strategy] = by_strategy.get(chunk.strategy, 0) + 1
    return {
        "count": len(lengths),
        "min_chars": min(lengths),
        "max_chars": max(lengths),
        "mean_chars": round(sum(lengths) / len(lengths), 1),
        "by_strategy": by_strategy,
    }
