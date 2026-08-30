"""Modules 5 et 6 — détection des structures juridiques et extraction des
articles (§10 et §11 du cahier des charges).

Le parser lit le texte page par page, reconnaît les en-têtes de subdivisions
(Titre, Chapitre, Section…) et les articles, puis reconstruit l'arborescence :

.. code-block:: text

    Titre II
        Chapitre III
            Section 2
                Article 45

Chaque article conserve son **contexte hiérarchique complet** et la page où il
commence, afin qu'on puisse toujours remonter à sa source (§33).

Deux précautions guident l'implémentation :

* le texte d'un article court de son en-tête jusqu'au prochain en-tête reconnu.
  Aucun contenu n'est jeté : ce qui précède le premier article est conservé
  comme préambule dans les nœuds de structure ;
* les lignes de sommaire et les formules de signature sont écartées de la
  détection, sinon la table des matières produirait des articles fantômes vides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import (
    Alinea,
    Article,
    Page,
    StructureLevel,
    StructureNode,
    level_depth,
)
from bldp.core.parser.rules import RuleSet, StructureRule, generic_ruleset
from bldp.utils import make_article_id, parse_number, slugify

logger = get_logger("parser")


# ---------------------------------------------------------------------------
# Texte linéarisé avec traçabilité des pages
# ---------------------------------------------------------------------------


@dataclass
class LineRef:
    """Une ligne de texte, avec sa provenance.

    Le parser travaille sur une liste de ``LineRef`` plutôt que sur une chaîne
    unique : c'est ce qui permet de dire, pour chaque article, *sur quelle page
    il commence et sur quelle page il finit*.
    """

    text: str
    page: int
    index: int          # rang de la ligne dans le document linéarisé
    char_start: int     # position dans le texte complet reconstitué
    char_end: int


def linearize(pages: Sequence[Page]) -> tuple[list[LineRef], str]:
    """Transforme les pages en une liste de lignes numérotées + le texte complet.

    Returns:
        ``(lignes, texte_complet)``. Les positions ``char_start``/``char_end``
        sont valides dans ``texte_complet``.
    """
    lines: list[LineRef] = []
    buffer: list[str] = []
    cursor = 0
    index = 0

    for page in pages:
        for raw_line in page.text.split("\n"):
            start = cursor
            end = start + len(raw_line)
            lines.append(
                LineRef(text=raw_line, page=page.page, index=index, char_start=start, char_end=end)
            )
            buffer.append(raw_line)
            cursor = end + 1  # +1 pour le saut de ligne
            index += 1

    return lines, "\n".join(buffer)


# ---------------------------------------------------------------------------
# Détection des en-têtes
# ---------------------------------------------------------------------------


@dataclass
class Heading:
    """Un en-tête reconnu dans le texte, avant construction de l'arbre."""

    rule: StructureRule
    level: StructureLevel
    number: Optional[str]
    heading: Optional[str]
    line: LineRef
    label: str
    inline_text: str = ""   # texte de l'article présent sur la même ligne


def _clean_number(raw: str | None) -> Optional[str]:
    """Normalise un numéro capturé (« 1 er » -> « 1er », « I I » -> « II »)."""
    if not raw:
        return None
    compact = re.sub(r"\s+", " ", raw.strip())
    compact = re.sub(r"(?<=\d)\s+(?=er\b|ère\b|ere\b)", "", compact, flags=re.IGNORECASE)
    return compact or None


def find_stop_index(lines: Sequence[LineRef], ruleset: RuleSet) -> Optional[int]:
    """Indice de la première ligne mettant fin à la partie normative.

    Les formules de promulgation (« Fait à Cotonou, le … ») ne sont pas des
    en-têtes : sans cette borne, elles seraient absorbées dans le texte du
    dernier article, qui porterait alors un contenu non normatif.
    """
    for line in lines:
        stripped = line.text.strip()
        if stripped and ruleset.is_stop_line(stripped):
            return line.index
    return None


def detect_headings(
    lines: Sequence[LineRef],
    ruleset: RuleSet,
    skip_toc: bool = True,
) -> list[Heading]:
    """Repère tous les en-têtes de subdivisions et d'articles.

    Args:
        lines: document linéarisé.
        ruleset: règles applicables (générique + juridiction).
        skip_toc: ignorer les lignes de sommaire, pour ne pas créer d'articles
            fantômes à partir de la table des matières.
    """
    headings: list[Heading] = []
    normative_ended = False

    for line in lines:
        stripped = line.text.strip()
        if not stripped:
            continue
        if skip_toc and ruleset.is_toc_line(stripped):
            continue
        if ruleset.is_stop_line(stripped):
            # La formule de promulgation clôt le corps normatif : ce qui suit
            # (signatures, mentions de publication) ne doit pas produire
            # d'articles fantômes.
            normative_ended = True
            continue

        found = ruleset.match_line(stripped)
        if not found:
            continue
        rule, match = found

        if rule.level is StructureLevel.ANNEXE:
            # Une annexe rouvre une zone structurée : son contenu est souvent
            # normatif (barèmes, formulaires, listes). L'ignorer perdrait du
            # texte que le document porte réellement.
            normative_ended = False
        elif normative_ended:
            continue

        groups = match.groupdict()
        number = _clean_number(groups.get("number"))
        tail = (groups.get("heading") or "").strip()

        # Une subdivision sans numéro est presque toujours un faux positif
        # (« section du contrat de travail » dans une phrase). Seules les
        # annexes peuvent se passer de numéro.
        if number is None and rule.level is not StructureLevel.ANNEXE:
            continue

        inline_text = ""
        heading_text: Optional[str] = tail or None
        if rule.level is StructureLevel.ARTICLE and tail:
            # Pour un article, ce qui suit le numéro est du contenu normatif,
            # pas un intitulé : on le conserve comme début du texte.
            inline_text = tail
            heading_text = None

        headings.append(
            Heading(
                rule=rule,
                level=rule.level,
                number=number,
                heading=heading_text,
                line=line,
                label=stripped,
                inline_text=inline_text,
            )
        )

    return headings


# ---------------------------------------------------------------------------
# Construction de l'arbre hiérarchique
# ---------------------------------------------------------------------------


def build_structure(
    headings: Sequence[Heading],
    document_id: str,
) -> tuple[list[StructureNode], dict[int, list[StructureNode]]]:
    """Construit les nœuds de structure et le contexte de chaque en-tête.

    Returns:
        ``(noeuds, contextes)`` où ``contextes[index_de_ligne]`` donne la pile
        d'ancêtres applicable à l'en-tête situé à cette ligne.
    """
    nodes: list[StructureNode] = []
    stack: list[StructureNode] = []
    contexts: dict[int, list[StructureNode]] = {}
    counters: dict[str, int] = {}

    for heading in headings:
        if heading.level is StructureLevel.ARTICLE:
            # L'article n'entre pas dans la pile : il est feuille par nature.
            contexts[heading.line.index] = list(stack)
            continue

        depth = level_depth(heading.level)
        # On dépile tout ce qui est de même niveau ou plus profond.
        while stack and level_depth(stack[-1].level) >= depth:
            stack.pop()

        key = heading.level.value
        counters[key] = counters.get(key, 0) + 1
        node_id = f"{document_id}_{key}_{slugify(heading.number or str(counters[key]), 20)}"
        if any(node.node_id == node_id for node in nodes):
            node_id = f"{node_id}_{counters[key]}"

        node = StructureNode(
            node_id=node_id,
            document_id=document_id,
            level=heading.level,
            number=heading.number,
            label=heading.label,
            heading=heading.heading,
            page=heading.line.page,
            char_start=heading.line.char_start,
            char_end=heading.line.char_end,
            parent_id=stack[-1].node_id if stack else None,
            depth=len(stack),
            path=[node.label for node in stack],
        )
        nodes.append(node)
        contexts[heading.line.index] = list(stack) + [node]
        stack.append(node)

    return nodes, contexts


def _context_field(context: Sequence[StructureNode], level: StructureLevel) -> Optional[str]:
    """Dernier nœud du niveau demandé dans la pile de contexte."""
    for node in reversed(context):
        if node.level is level:
            return node.label
    return None


# ---------------------------------------------------------------------------
# Découpage en alinéas
# ---------------------------------------------------------------------------


def split_alineas(text: str, pattern: Optional[re.Pattern[str]]) -> list[Alinea]:
    """Découpe le texte d'un article en alinéas, dans l'ordre (§11).

    Deux signaux sont utilisés : une numérotation explicite en début de ligne
    (« 1° », « a) », « - ») et, à défaut, les sauts de paragraphe. Si aucun des
    deux n'est présent, l'article compte un seul alinéa — on ne découpe pas
    arbitrairement une phrase juridique.
    """
    if not text.strip():
        return []

    blocks: list[tuple[Optional[str], list[str]]] = []
    current_number: Optional[str] = None
    current: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append((current_number, current))
                current, current_number = [], None
            continue

        match = pattern.match(stripped) if pattern else None
        if match:
            if current:
                blocks.append((current_number, current))
            current_number = match.group("number").strip()
            current = [stripped]
        else:
            current.append(stripped)

    if current:
        blocks.append((current_number, current))

    return [
        Alinea(index=index, text=" ".join(chunk).strip(), number=number)
        for index, (number, chunk) in enumerate(blocks)
        if " ".join(chunk).strip()
    ]


# ---------------------------------------------------------------------------
# Extraction des articles
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """Sortie complète du parser pour un document."""

    document_id: str
    structure: list[StructureNode] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    #: Texte antérieur au premier article (visas, intitulé officiel).
    preamble: str = ""
    #: Texte postérieur à la formule de promulgation (signatures, mentions de
    #: publication). Conservé pour ne rien perdre, mais hors de tout article.
    epilogue: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def article_count(self) -> int:
        return len(self.articles)


def parse_document(
    pages: Sequence[Page],
    document_id: str,
    config: Config,
    ruleset: RuleSet | None = None,
    source_file: str = "",
) -> ParseResult:
    """Détecte la structure et extrait les articles d'un document.

    Args:
        pages: pages **nettoyées** (module 5).
        document_id: identifiant du document.
        config: configuration (section ``parser``).
        ruleset: règles à appliquer ; par défaut le jeu générique.
        source_file: nom du fichier d'origine, reporté sur chaque article.

    Returns:
        Le résultat de l'analyse : nœuds de structure, articles, préambule et
        avertissements.
    """
    ruleset = ruleset or generic_ruleset()
    result = ParseResult(document_id=document_id)

    if not pages:
        return result

    detect_articles = bool(config.get("parser.detect_articles", True))
    detect_hierarchy = bool(config.get("parser.detect_hierarchy", True))
    detect_alineas = bool(config.get("parser.detect_alineas", True))
    min_chars = int(config.get("parser.min_article_chars", 20))
    max_chars = int(config.get("parser.max_article_chars", 40000))

    lines, full_text = linearize(pages)
    headings = detect_headings(lines, ruleset)

    if not detect_hierarchy:
        headings = [h for h in headings if h.level is StructureLevel.ARTICLE]

    nodes, contexts = build_structure(headings, document_id)
    result.structure = nodes

    if not detect_articles:
        return result

    article_headings = [h for h in headings if h.level is StructureLevel.ARTICLE]
    if not article_headings:
        result.warnings.append(
            "aucun article détecté : le document n'est peut-être pas un texte "
            "normatif, ou son format n'est pas couvert par les règles actuelles"
        )
        logger.warning("%s : aucun article détecté", document_id)
        result.preamble = full_text.strip()
        return result

    # Le préambule est tout ce qui précède le premier article : jamais jeté.
    first_index = article_headings[0].line.index
    result.preamble = "\n".join(l.text for l in lines[:first_index]).strip()

    # Bornes : un article s'arrête au prochain en-tête, quel que soit son
    # niveau — ou à la formule de promulgation, qui clôt la partie normative.
    stop_index = find_stop_index(lines, ruleset)
    boundaries = sorted({h.line.index for h in headings})
    if stop_index is not None:
        boundaries = sorted({*boundaries, stop_index})
        # L'épilogue court de la promulgation jusqu'à l'en-tête suivant — une
        # annexe, le cas échéant — et non jusqu'à la fin du document.
        epilogue_end = _next_boundary(boundaries, stop_index, len(lines))
        result.epilogue = "\n".join(
            l.text for l in lines[stop_index:epilogue_end]
        ).strip()

    articles: list[Article] = []

    for position, heading in enumerate(article_headings):
        start_index = heading.line.index
        end_index = _next_boundary(boundaries, start_index, len(lines))

        body_lines = lines[start_index + 1 : end_index]
        parts = [heading.inline_text] if heading.inline_text else []
        parts.extend(l.text for l in body_lines)
        text = "\n".join(parts).strip()

        page_start = heading.line.page
        page_end = body_lines[-1].page if body_lines else page_start
        char_end = body_lines[-1].char_end if body_lines else heading.line.char_end

        context = contexts.get(start_index, [])
        number = heading.number or str(position + 1)

        article = Article(
            article_id=make_article_id(document_id, number, position),
            document_id=document_id,
            article_number=number,
            text=text,
            label=heading.label,
            position=position,
            page_start=page_start,
            page_end=page_end,
            char_start=heading.line.char_start,
            char_end=char_end,
            partie=_context_field(context, StructureLevel.PARTIE),
            livre=_context_field(context, StructureLevel.LIVRE),
            title=_context_field(context, StructureLevel.TITRE),
            subtitle=_context_field(context, StructureLevel.SOUS_TITRE),
            chapter=_context_field(context, StructureLevel.CHAPITRE),
            section=_context_field(context, StructureLevel.SECTION),
            subsection=_context_field(context, StructureLevel.SOUS_SECTION),
            annexe=_context_field(context, StructureLevel.ANNEXE),
            hierarchy_path=[node.label for node in context],
            numeric_value=parse_number(number),
            source_file=source_file or (pages[0].source_file if pages else ""),
        )

        if detect_alineas:
            article.alineas = split_alineas(text, ruleset.alinea_pattern)

        if len(text) < min_chars:
            article.warnings.append("article_potentiellement_incomplet")
        if len(text) > max_chars:
            article.warnings.append("article_anormalement_long_fusion_probable")
        if article.numeric_value is None:
            article.warnings.append("numero_article_non_interpretable")

        articles.append(article)

    articles = _disambiguate_ids(articles)
    result.articles = articles

    logger.info("%s : %d article(s) détecté(s)", document_id, len(articles))
    for article in articles:
        for warning in article.warnings:
            if warning == "article_potentiellement_incomplet":
                logger.warning(
                    "Article %s potentiellement incomplet (%d caractères)",
                    article.article_number,
                    len(article.text),
                )

    return result


def _next_boundary(boundaries: Sequence[int], current: int, total: int) -> int:
    """Indice du prochain en-tête après ``current``, ou la fin du document."""
    for boundary in boundaries:
        if boundary > current:
            return boundary
    return total


def _disambiguate_ids(articles: Sequence[Article]) -> list[Article]:
    """Rend les identifiants d'articles uniques dans le document.

    Un même numéro peut réapparaître (annexes, codes en plusieurs livres) : on
    suffixe alors par le rang, ce qui reste stable d'une exécution à l'autre.
    """
    seen: dict[str, int] = {}
    output: list[Article] = []
    for article in articles:
        base = article.article_id
        count = seen.get(base, 0)
        if count:
            article.article_id = f"{base}_{count + 1}"
            article.warnings.append("numero_article_duplique_dans_le_document")
        seen[base] = count + 1
        output.append(article)
    return output


# ---------------------------------------------------------------------------
# Contrôle de numérotation (§24)
# ---------------------------------------------------------------------------


def check_numbering(articles: Sequence[Article]) -> list[str]:
    """Repère les ruptures de numérotation des articles.

    Une séquence ``1, 2, 4`` fait remonter un signalement : c'est le symptôme
    typique d'un article manqué à l'extraction ou d'une page absente. Le parser
    **signale** sans corriger — corriger reviendrait à inventer du droit.
    """
    anomalies: list[str] = []
    numbered = [a for a in articles if a.numeric_value is not None]
    if len(numbered) < 2:
        return anomalies

    previous = numbered[0]
    for article in numbered[1:]:
        gap = article.numeric_value - previous.numeric_value
        if gap <= 0:
            anomalies.append(
                f"numérotation non croissante : article {previous.article_number} "
                f"suivi de l'article {article.article_number}"
            )
        elif gap > 1.0 and float(gap).is_integer():
            missing = int(previous.numeric_value) + 1
            last_missing = int(article.numeric_value) - 1
            span = (
                f"{missing}" if missing == last_missing else f"{missing} à {last_missing}"
            )
            anomalies.append(
                f"article(s) {span} manquant(s) entre les articles "
                f"{previous.article_number} et {article.article_number}"
            )
        previous = article

    return anomalies


def articles_by_page(articles: Iterable[Article]) -> dict[int, list[Article]]:
    """Regroupe les articles par page de début, pour la validation humaine."""
    grouped: dict[int, list[Article]] = {}
    for article in articles:
        grouped.setdefault(article.page_start, []).append(article)
    return grouped
