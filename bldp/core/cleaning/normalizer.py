"""Module 5 — nettoyage du texte extrait (§9 du cahier des charges).

Le nettoyage supprime les **artefacts techniques** (espaces multiples, césures,
en-têtes et pieds de page répétés, numéros de page isolés, caractères de
contrôle) sans jamais toucher au **contenu juridique**.

Deux garde-fous structurent tout le module :

1. *Rien n'est supprimé sans preuve.* Un en-tête n'est retiré que s'il se répète
   sur une proportion suffisante de pages ; un numéro de page n'est retiré que
   s'il est seul sur sa ligne, en zone de marge.
2. *En cas de doute, on conserve.* Toute ligne susceptible de porter du droit
   — article, alinéa, référence légale, date, montant, sanction, condition —
   est protégée : aucune règle de suppression ne peut l'atteindre.

Chaque transformation est journalisée dans un :class:`CleaningReport`, de sorte
qu'on puisse toujours répondre à la question « qu'est-ce qui a été enlevé, et
pourquoi ? ». Le texte d'origine reste par ailleurs disponible dans
``Page.raw_text``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import Page
from bldp.utils import normalize_number_dashes

logger = get_logger("cleaning")


# ---------------------------------------------------------------------------
# Motifs protégés — ce que le nettoyage ne doit jamais atteindre (§9)
# ---------------------------------------------------------------------------

#: Une ligne qui correspond à l'un de ces motifs porte potentiellement du droit.
PROTECTED_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Articles et subdivisions
    re.compile(r"\bart(?:icle)?s?\.?\s*(?:\d|premier|1er|[IVXLC])", re.IGNORECASE),
    re.compile(r"\balin[ée]as?\b", re.IGNORECASE),
    re.compile(r"\bparagraphes?\b", re.IGNORECASE),
    # Références légales
    re.compile(r"\b(?:loi|d[ée]cret|arr[êe]t[ée]|ordonnance|code|constitution)\b", re.IGNORECASE),
    re.compile(r"\bn[°o]\s*\d", re.IGNORECASE),
    # Dates
    re.compile(r"\b\d{1,2}\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|"
               r"septembre|octobre|novembre|d[ée]cembre)\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    # Montants et peines
    re.compile(r"\d[\d\s.,]*\s*(?:francs?|f\s*cfa|fcfa|xof|euros?|%)", re.IGNORECASE),
    re.compile(r"\b(?:amende|emprisonnement|peine|sanction|nullit[ée]|d[ée]ch[ée]ance)\b",
               re.IGNORECASE),
    # Conditions et exceptions juridiques
    re.compile(r"\b(?:sauf|toutefois|nonobstant|sous r[ée]serve|[àa] peine de|"
               r"par d[ée]rogation|except[ée])\b", re.IGNORECASE),
)


def is_protected(line: str) -> bool:
    """Vrai si la ligne porte un marqueur de contenu juridique.

    Utilisé comme veto : aucune règle de suppression ne s'applique à une ligne
    protégée, même si elle ressemble par ailleurs à un en-tête répété.
    """
    return any(pattern.search(line) for pattern in PROTECTED_PATTERNS)


# ---------------------------------------------------------------------------
# Motifs d'artefacts
# ---------------------------------------------------------------------------

#: Numéro de page isolé : « 12 », « - 12 -», « Page 12 », « 12 / 340 ».
PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:[-–—\[(]\s*)?(?:page\s*)?(\d{1,4})(?:\s*[/sur]{1,3}\s*\d{1,4})?"
    r"\s*(?:[-–—\])])?\s*$",
    re.IGNORECASE,
)

#: Numéro de page en chiffres romains, fréquent dans les préambules.
ROMAN_PAGE_RE = re.compile(r"^\s*[-–—(\[]?\s*[ivxlcdm]{1,7}\s*[-–—)\]]?\s*$", re.IGNORECASE)

#: Césure de fin de ligne : « respon-\nsabilité ».
HYPHENATION_RE = re.compile(r"(\w)[-­]\s*\n\s*(\w)")

#: Ligne composée uniquement de ponctuation décorative ou de points de conduite.
DECORATIVE_RE = re.compile(r"^[\s.…_\-–—=*~+·•|]{3,}$")

#: Espaces insécables et variantes typographiques ramenés à l'espace simple.
_SPACE_VARIANTS = dict.fromkeys(
    map(ord, "          "
             "     　"),
    " ",
)

#: Caractères de largeur nulle et marques de direction, purement parasites.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

#: Guillemets et tirets exotiques normalisés (sans changer le sens).
_PUNCT_NORMALISATION = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "″": '"', "′": "'",
}

#: Confusions OCR corrigées **uniquement** dans des contextes non ambigus.
#: Chaque règle est un couple (motif, remplacement) volontairement étroit :
#: on préfère laisser passer une coquille que corrompre un numéro d'article.
#:
#: Les motifs larges portent un ``(?!Article)`` : sans lui, ils
#: reconnaissent aussi la graphie **correcte**, et le remplacement — sans
#: effet sur le texte — était pourtant compté comme une correction. Un
#: document sain semblait alors avoir été réparé.
OCR_CONFUSION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # « Artic1e 45 », « ArticIe 45 » -> « Article 45 »
    (re.compile(r"(?!Article\b)\bArtic[l1I|]e\b"), "Article"),
    (re.compile(r"(?!Article\b)\bArtic[l1I|][e3]\b"), "Article"),
    # « Article » sur sept lettres, chacune dans son petit jeu de confusions
    # relevées sur des scans réels : « Arlicle » (t lu l), « Artlcle »,
    # « Arllcle », « ArtIcle », « Articte », « Articlè ».
    #
    # Ce n'est pas une variante marginale : dans un seul code électoral,
    # « Arlicle » apparaît **75 fois**. Sans cette règle, autant d'articles
    # absents du corpus.
    #
    # Le motif reste étroit — sept lettres exactes, bornées par \b, casse
    # respectée — et n'atteint aucun mot français : « Artisan », « Artifice »,
    # « Articulation » n'ont pas cette forme. Le pluriel « Articles » lui
    # échappe, ce qui convient : ce n'est jamais un en-tête.
    (re.compile(r"(?!Article\b)\bA[rn][tl][il1I][cd][lt1I][eè3]\b"), "Article"),
    (re.compile(r"(?!ARTICLE\b)\bA[RN][TL][IL1][CD][LT1][EÈ3]\b"), "ARTICLE"),
    # Formes de six lettres, où une lettre a fusionné avec sa voisine.
    (re.compile(r"\bArti(?:cte|de|cie|clc|cle\.|ele)\b"), "Article"),
    # « Artiole », « ARTIOLE » : le c lu o. Lot 1, tranche T002 : 13 articles de la Loi
    # fondamentale de 1977 (ordonnance 77-32) manquaient pour cette seule lecture.
    (re.compile(r"\bArtiole\b"), "Article"),
    # Tranche T003 : « Articlo », « Articolo » (e lu o), « A article 52.- » (A parasite en
    # début de ligne), « _ticle ler.- » (« Ar » perdu). Toujours en tête de ligne et devant
    # un numéro, là où seul un en-tête d'article a ce sens.
    (re.compile(r"\bArtic(?:o)?lo\b(?=\s*[0-9Il]|\s+premier)"), "Article"),
    (re.compile(r"\bARTIC(?:O)?LO\b(?=\s*[0-9Il]|\s+PREMIER)"), "ARTICLE"),
    (re.compile(r"(?m)^A\s+(?:a|A)rticle(?=\s+\d)"), "Article"),
    (re.compile(r"(?m)^_+ticle(?=\s+(?:\d|[Il1][e]r\b))"), "Article"),
    # « VU le Décrit n° 215/PR » : le e de Décret frappé ou lu i. Devant un numéro
    # seulement, où « décrit » (participe) n'a pas de sens. Tranches T002-T003.
    (re.compile(r"\bDécrit(?=\s*n\s*[°o])", re.IGNORECASE), "Décret"),
    (re.compile(r"\bDECRIT(?=\s*N\s*[°O])"), "DECRET"),
    (re.compile(r"\bARTIOLE\b"), "ARTICLE"),
    (re.compile(r"(?!Article\b)\bAr[tU][Ui]?cle\b"), "Article"),
    (re.compile(r"\bAdi[cd]le\b"), "Article"),
    # Le « r » avalé : « Aticle », « Atticle », « Atlicle », « Aficle ».
    #
    # Mesuré sur la loi 2024-09 du corpus SGG : 17 en-têtes sur 54 portaient
    # l'une de ces formes, soit **27 % des articles du document** invisibles
    # au parseur. Les règles précédentes exigent toutes un « r » ou un « d »
    # en deuxième position et les laissaient donc passer.
    #
    # La forme commune est « A », une ou deux lettres hautes et étroites là
    # où le « rt » a fondu, puis « icle ». Le motif reste borné par \b et
    # n'atteint aucun mot français : aucun n'a cette forme hors « Article ».
    (re.compile(r"(?!Article\b)\bA[tflr]{1,2}[il1]cle\b"), "Article"),
    (re.compile(r"(?!ARTICLE\b)\bA[TFLR]{1,2}[IL1]CLE\b"), "ARTICLE"),
    (re.compile(r"\bARTI(?:CTE|DE|CIE|CLC|ELE)\b"), "ARTICLE"),
    # « ARTICI E » -> « ARTICLE »
    (re.compile(r"(?!ARTICLE\b)\bARTIC[L1I|]\s?E\b"), "ARTICLE"),
    # « Article l2 », « Article I4 » : le 1 initial du numéro lu l ou I. Le
    # contexte « Article » rend la substitution sûre.
    (re.compile(r"(?<=\bArticle )[lI](?=\d)"), "1"),
    (re.compile(r"\bArticle\s+leI\b"), "Article 1er"),
    # « ARTICLE Ier.- », « Article ler.- », « ARTICLE Icr : » : l'article premier des
    # textes dactylographiés (« Ier », lu aussi « ler », « Icr »). Lot 1, tranche T002 :
    # l'article premier manquait ainsi dans 9 ordonnances sur 50.
    (re.compile(r"\b(Article|ARTICLE)[ \t_]*[Il|][ \t]?(?:er|ER|cr|èr)(?!\w)"), r"\1 1er"),
    # Zéro/O au milieu d'un nombre : « 2O26 » -> « 2026 »
    (re.compile(r"(?<=\d)[OoQ](?=\d)"), "0"),
    # Un/l/I au milieu d'un nombre : « 2l26 » -> « 2126 »
    (re.compile(r"(?<=\d)[lI|](?=\d)"), "1"),
    # Espace parasite dans « n ° 12 »
    (re.compile(r"\bn\s*[°ºo]\s*(?=\d)", re.IGNORECASE), "n° "),
    # Ligature mal décodée
    (re.compile(r"ﬁ"), "fi"),
    (re.compile(r"ﬂ"), "fl"),
    # --- Mots-clés de subdivision ----------------------------------------
    # « TIVRE » pour « LIVRE » (L lu T) : sans cette règle, le niveau LIVRE
    # d'un code disparaissait entièrement de la hiérarchie. Aucune de ces
    # graphies n'est un mot français.
    (re.compile(r"\bTIVRE\b"), "LIVRE"),
    # Pas de ``\b`` final : le numéro est justement **collé** au mot-clé
    # (« SECfIONl »), donc il n'y a pas de frontière de mot où l'attendre.
    # Aucune de ces graphies n'existe en français, l'ancrage initial suffit.
    (re.compile(r"\bSEC[fF]ION"), "SECTION"),
    (re.compile(r"\bPARAGRAPBE"), "PARAGRAPHE"),
    (re.compile(r"\bT[IÏ]IRE"), "TITRE"),
    # Mot-clé soudé à son numéro : « TITREll », « CHAPITREI », « SECTIONl ».
    # L'OCR perd l'espace ; on la rétablit sans rien ajouter d'autre. Le
    # numéro reste tel quel — s'il est illisible, le contrôle qualité le dira.
    (
        re.compile(
            r"\b(TITRE|CHAPITRE|SECTION|PARAGRAPHE|LIVRE|ANNEXE|PARTIE)"
            r"(?=[IVXLCDMl\d])"
        ),
        r"\1 ",
    ),
)


# ---------------------------------------------------------------------------
# Rapport de nettoyage
# ---------------------------------------------------------------------------


@dataclass
class CleaningReport:
    """Journal des transformations appliquées, pour audit (§33)."""

    document_id: str = ""
    pages_processed: int = 0
    chars_before: int = 0
    chars_after: int = 0
    removed_headers: list[str] = field(default_factory=list)
    removed_footers: list[str] = field(default_factory=list)
    removed_page_numbers: int = 0
    removed_decorative_lines: int = 0
    joined_hyphenations: int = 0
    rejoined_article_headers: int = 0
    joined_wrapped_lines: int = 0
    ocr_corrections: int = 0
    #: Corrections OCR appliquees a un texte pourtant classe *natif* :
    #: signe que la couche texte du PDF vient d'un OCR anterieur.
    native_text_repaired: int = 0
    control_chars_removed: int = 0
    #: Marques de balisage d'un lecteur vision-langage ramenees a du texte
    #: (LaTeX, HTML, Markdown) — voir :func:`strip_reader_markup`.
    reader_markup_removed: int = 0
    protected_lines_kept: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def chars_removed(self) -> int:
        return max(0, self.chars_before - self.chars_after)

    @property
    def removal_ratio(self) -> float:
        """Part du texte supprimée — au-delà de quelques pour cent, on alerte."""
        if not self.chars_before:
            return 0.0
        return self.chars_removed / self.chars_before

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "pages_processed": self.pages_processed,
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "chars_removed": self.chars_removed,
            "removal_ratio": round(self.removal_ratio, 4),
            "removed_headers": self.removed_headers,
            "removed_footers": self.removed_footers,
            "removed_page_numbers": self.removed_page_numbers,
            "removed_decorative_lines": self.removed_decorative_lines,
            "joined_hyphenations": self.joined_hyphenations,
            "rejoined_article_headers": self.rejoined_article_headers,
            "joined_wrapped_lines": self.joined_wrapped_lines,
            "ocr_corrections": self.ocr_corrections,
            "native_text_repaired": self.native_text_repaired,
            "control_chars_removed": self.control_chars_removed,
            "protected_lines_kept": self.protected_lines_kept,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Transformations élémentaires
# ---------------------------------------------------------------------------


def strip_control_chars(text: str) -> tuple[str, int]:
    """Retire les caractères de contrôle, sauf les sauts de ligne et tabulations."""
    kept: list[str] = []
    removed = 0
    for char in text:
        if char in "\n\t":
            kept.append(char)
        elif char == "\r":
            removed += 1  # normalisé plus loin en \n
        elif unicodedata.category(char) in {"Cc", "Cf"}:
            removed += 1
        else:
            kept.append(char)
    return "".join(kept), removed


def normalize_unicode(text: str) -> str:
    """Normalise l'Unicode en NFC et uniformise espaces et guillemets.

    Purement typographique : aucun mot n'est modifié.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_ZERO_WIDTH).translate(_SPACE_VARIANTS)
    for source, replacement in _PUNCT_NORMALISATION.items():
        text = text.replace(source, replacement)
    # Les tirets **entre chiffres** sont des séparateurs de numérotation : les
    # ramener au trait d'union rend « n° 2025 — 18 » reconnaissable. Les tirets
    # d'incise, eux, restent intacts (voir normalize_number_dashes).
    text = normalize_number_dashes(text)
    return unicodedata.normalize("NFC", text)


def fix_hyphenation(text: str) -> tuple[str, int]:
    """Recolle les mots coupés en fin de ligne.

    La règle est volontairement stricte : lettre + tiret + fin de ligne +
    lettre. Les tirets suivis d'un chiffre ou d'une majuscule (« 2026-\\n001 »,
    « Franco-\\nBéninois ») ne sont pas touchés, car le trait d'union y est
    signifiant.
    """
    count = 0

    def _join(match: re.Match[str]) -> str:
        nonlocal count
        left, right = match.group(1), match.group(2)
        if left.isdigit() or right.isdigit() or right.isupper():
            return match.group(0)
        count += 1
        return left + right

    return HYPHENATION_RE.sub(_join, text), count


#: En-tête d'article **incomplet**, c'est-à-dire réduit au mot-clé, ou au
#: mot-clé et au numéro, sans le contenu qui devrait suivre.
INCOMPLETE_ARTICLE_HEADER_RE = re.compile(
    r"^\s*(?:article|art\.?)"
    r"(?:\s*\d{1,4}(?:\s*[-–—]\s*\d{1,4})?(?:\s*(?:er|bis|ter|nouveau))?)?"
    r"\s*:?\s*$",
    re.IGNORECASE,
)

#: Début plausible de la suite d'un en-tête éclaté : un numéro, ou le
#: deux-points isolé qui le suit.
_HEADER_CONTINUATION_RE = re.compile(
    r"^\s*(?::|\d{1,4}|[IVXLCDM]{1,7}\b|premier|1\s*er)", re.IGNORECASE
)

#: En-tête **exploitable** par le parser : le mot-clé suivi d'un numéro.
#: Le contenu peut rester sur les lignes suivantes — le parser sait le
#: rattacher. Il suffit donc de reconstituer « Article 88 : ».
_USABLE_ARTICLE_HEADER_RE = re.compile(
    r"^\s*(?:article|art\.?)\s*(?:\d{1,4}|[IVXLCDM]{1,7}\b|premier|1\s*er)",
    re.IGNORECASE,
)

#: Nombre maximal de lignes recollées pour reconstituer un seul en-tête.
_MAX_HEADER_MERGES = 3


def rejoin_split_article_headers(text: str) -> tuple[str, int]:
    """Recolle les en-têtes d'article éclatés par l'OCR.

    Sur des scans réels, Tesseract rend fréquemment ::

        Article
        88
        :

    au lieu de « Article 88 : ». Le parser, qui raisonne ligne par ligne, ne
    reconnaissait alors pas l'article — et celui-ci **disparaissait du corpus
    sans avertissement**. Le recollage général (:func:`join_wrapped_lines`) ne
    règle pas le cas : il refuse de joindre une ligne commençant par un
    chiffre, précisément pour ne pas avaler un numéro de page.

    La règle est volontairement étroite : on ne joint que si la ligne courante
    est un en-tête d'article *incomplet* et que la suivante en est la
    continuation plausible (un numéro, ou le deux-points). Au plus trois
    fusions, pour ne jamais absorber un paragraphe entier.

    Returns:
        ``(texte, nombre_d_en-têtes_reconstitués)``.
    """
    lines = text.split("\n")
    output: list[str] = []
    rejoined = 0
    index = 0

    while index < len(lines):
        current = lines[index].strip()
        if not current or not INCOMPLETE_ARTICLE_HEADER_RE.match(current):
            output.append(lines[index])
            index += 1
            continue

        merged = current
        lookahead = index + 1
        merges = 0
        while (
            lookahead < len(lines)
            and merges < _MAX_HEADER_MERGES
            and INCOMPLETE_ARTICLE_HEADER_RE.match(merged)
        ):
            candidate = lines[lookahead].strip()
            if not candidate:
                lookahead += 1
                continue
            if not _HEADER_CONTINUATION_RE.match(candidate):
                break
            # Le deux-points se colle au numéro ; le reste prend une espace.
            separator = "" if candidate.startswith(":") else " "
            merged = f"{merged}{separator}{candidate}"
            lookahead += 1
            merges += 1

        # Succès dès que l'en-tête porte un numéro : « Article 88 : » suffit au
        # parser, qui rattachera le corps resté sur les lignes suivantes.
        became_usable = _USABLE_ARTICLE_HEADER_RE.match(
            merged
        ) and not _USABLE_ARTICLE_HEADER_RE.match(current)

        if merges and became_usable:
            output.append(merged)
            rejoined += 1
            index = lookahead
        else:
            output.append(lines[index])
            index += 1

    return "\n".join(output), rejoined


def join_wrapped_lines(text: str) -> tuple[str, int]:
    """Fusionne les retours à la ligne artificiels d'une même phrase.

    Une ligne est jointe à la suivante seulement si elle ne se termine pas par
    une ponctuation forte **et** que la suivante commence par une minuscule.
    Un début de ligne en majuscule, un tiret d'énumération ou un numéro
    interrompt la fusion : ce sont des débuts d'unités juridiques probables.
    """
    lines = text.split("\n")
    output: list[str] = []
    joined = 0

    for line in lines:
        stripped = line.strip()
        if not output or not stripped:
            output.append(stripped)
            continue

        previous = output[-1]
        if (
            previous
            and not re.search(r"[.;:!?»\"')\]]\s*$", previous)
            and not previous.endswith(":")
            and stripped[:1].islower()
            and not stripped.startswith(("-", "–", "—", "•", "*"))
        ):
            output[-1] = f"{previous} {stripped}"
            joined += 1
        else:
            output.append(stripped)

    return "\n".join(output), joined


def collapse_whitespace(text: str) -> str:
    """Réduit les espaces multiples et limite les lignes vides consécutives."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Balisage des lecteurs vision-langage (PaddleOCR-VL…)
# ---------------------------------------------------------------------------

#: Un segment ``$ … $`` n'est traité que s'il porte une marque LaTeX (``\``,
#: ``^``, ``_{``) : « 5 $ et 6 $ » reste tel quel.
_LATEX_SEGMENT_RE = re.compile(r"(?P<avant>[ \t]*)\$(?P<contenu>[^$\n]{0,160}?)\$")
_LATEX_MARQUE_RE = re.compile(r"\\|\^|_\{")
_LATEX_COMMANDE_A_CONTENU_RE = re.compile(
    r"\\(?:underline|overline|text|textbf|textit|textrm|textup|mathrm|mathbf|mathit|"
    r"mathsf|operatorname|emph|mbox|hbox)\s*\{([^{}]*)\}"
)
_LATEX_INDICE_RE = re.compile(r"[\^_]\{([^{}]*)\}")
_LATEX_INDICE_SIMPLE_RE = re.compile(r"[\^_](\w)")
_LATEX_SYMBOLES = (
    ("\\%", "%"), ("\\&", "&"), ("\\times", "×"), ("\\cdot", "·"), ("\\circ", "°"),
    ("\\degree", "°"), ("\\pm", "±"), ("\\leq", "≤"), ("\\geq", "≥"), ("\\neq", "≠"),
    ("\\Delta", "Δ"), ("\\delta", "δ"), ("\\alpha", "α"), ("\\beta", "β"), ("\\mu", "µ"),
    ("\\qquad", " "), ("\\quad", " "), ("\\,", " "), ("\\;", " "), ("\\!", ""), ("\\ ", " "),
)
_HTML_EXPOSANT_RE = re.compile(r"[ \t]*<(sup|sub)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL)
_HTML_LIGNE_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELLULE_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_HTML_BALISE_RE = re.compile(
    r"</?(?:table|thead|tbody|tfoot|tr|td|th|br|div|span|p|b|i|u|center|strong|em)\b[^>]*/?>",
    re.IGNORECASE,
)
#: LaTeX resté hors de ses « $ » : « N^{\circ} », « 1^{er} » (lot 1, reprise T002R1).
_LATEX_NU_RE = re.compile(r"\^\{\\circ\}|\^\{(?:\\text\{)?(er|re|e|ème|eme)\}?\}")
#: « $ ORDONNANCE $ » : des « $ » autour de mots en capitales seulement.
#: « \underline{9} », « \text{…} » restés hors de leurs « $ » (tranche T003).
_LATEX_COMMANDE_NUE_RE = re.compile(r"\\(?:underline|overline|text|textbf|textit|mathrm|emph)\s*\{([^{}]*)\}")
#: Entités HTML laissées par le lecteur : « &#x27; », « &amp; », « &quot; » (tranche T003).
_ENTITE_HTML_RE = re.compile(r"&(?:#x?[0-9A-Fa-f]{1,6}|amp|quot|apos|lt|gt|nbsp);")
_DOLLARS_CAPITALES_RE = re.compile(r"\$[ \t]*([A-ZÀ-Ý][A-ZÀ-Ý '’.-]{2,60}?)[ \t]*\$")
_MD_GRAS_RE = re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*")
_MD_TITRE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)


def _latex_en_texte(contenu: str) -> str:
    for _ in range(4):                       # imbrications : \underline{\text{…}}
        contenu, n = _LATEX_COMMANDE_A_CONTENU_RE.subn(r"\1", contenu)
        if not n:
            break
    contenu = _LATEX_INDICE_RE.sub(r"\1", contenu)
    contenu = _LATEX_INDICE_SIMPLE_RE.sub(r"\1", contenu)
    for commande, symbole in _LATEX_SYMBOLES:
        contenu = contenu.replace(commande, symbole)
    # Commande inconnue : on garde son nom — rien ne disparaît en silence.
    contenu = re.sub(r"\\([A-Za-z]+)", r"\1", contenu)
    contenu = contenu.replace("{", "").replace("}", "")
    return " ".join(contenu.split())


def _table_en_texte(table: str) -> str:
    lignes = []
    for ligne in _HTML_LIGNE_RE.findall(table):
        cellules = [" ".join(_HTML_BALISE_RE.sub(" ", c).split()) for c in _HTML_CELLULE_RE.findall(ligne)]
        if any(cellules):
            lignes.append(" | ".join(cellules))
    if not lignes:                           # tableau mal formé : on garde le texte, sans les balises
        return " ".join(_HTML_BALISE_RE.sub(" ", table).split())
    return "\n" + "\n".join(lignes) + "\n"


def strip_reader_markup(text: str) -> tuple[str, int]:
    """Ramène à du texte le balisage d'un lecteur vision-langage.

    PaddleOCR-VL et ses semblables rendent les exposants et le souligné en
    LaTeX (« DU $ 1^{er} $ JUIN 2022 »), les tableaux en HTML, le gras et les
    titres en Markdown. Le pipeline lit du texte : sans ceci, la date de
    l'acte et un article souligné lui échappaient (lot 1, tranche T001).

    Seules les marques tombent, jamais le contenu : une commande LaTeX
    inconnue garde son nom, une cellule de tableau son texte. Un texte sans
    balisage ressort inchangé, et le compteur à zéro.
    """
    if not text:
        return text, 0
    compte = [0]

    def segment(m: re.Match[str]) -> str:
        contenu = m.group("contenu")
        if not _LATEX_MARQUE_RE.search(contenu):
            return m.group(0)
        compte[0] += 1
        texte = _latex_en_texte(contenu)
        # « 1 $ ^{er} $ » : un exposant isolé se colle à ce qui précède → « 1er ».
        return texte if re.match(r"\s*[\^_]", contenu) else m.group("avant") + texte

    if "$" in text:
        text = _LATEX_SEGMENT_RE.sub(segment, text)
    if "\\" in text:
        text, n = _LATEX_COMMANDE_NUE_RE.subn(r"\1", text)
        compte[0] += n
    if "&" in text:
        import html as _html
        text, n = _ENTITE_HTML_RE.subn(lambda m: _html.unescape(m.group(0)), text)
        compte[0] += n
    if "^{" in text:
        text, n = _LATEX_NU_RE.subn(lambda m: m.group(1) or "°", text)
        compte[0] += n
    if "$" in text:
        text, n = _DOLLARS_CAPITALES_RE.subn(r"\1", text)
        compte[0] += n
    if "<" in text:
        text, n = _HTML_EXPOSANT_RE.subn(lambda m: m.group(2).strip(), text)
        compte[0] += n
        text, n = _HTML_TABLE_RE.subn(lambda m: _table_en_texte(m.group(1)), text)
        compte[0] += n
        text, n = _HTML_BALISE_RE.subn("", text)
        compte[0] += n
    if "**" in text:
        text, n = _MD_GRAS_RE.subn(r"\1", text)
        compte[0] += n
    if "#" in text:
        text, n = _MD_TITRE_RE.subn("", text)
        compte[0] += n
    return text, compte[0]


#: Le « O » décoratif des en-têtes dactylographiés (« ⃞ R D O N N E »), dessiné comme
#: une case, est lu « (二) », « (一) », « (_) » ou disparaît : « RDONNANCE », « R D O N N E ».
#: Lot 1, tranche T002 : la moitié des ordonnances de 1963-1968. On ne rend le « O » que
#: devant « R D O N N », jamais ailleurs ; aucun mot français ne commence par « RDONN ».
_O_DECORATIF_RE = re.compile(r"(?:\(\s*[二一_\-0OC□口○◯〇]?\s*\)|[二一□口○◯〇])?[ \t]*(?<![A-Za-zÀ-ÿ])(?<!O )(R(\s?)D\s?O\s?N\s?N)")


def restore_decorative_o(text: str) -> tuple[str, int]:
    """Rend le « O » décoratif de « ORDONNE » / « ORDONNANCE » (voir ``_O_DECORATIF_RE``)."""
    return _O_DECORATIF_RE.subn(lambda m: "O" + m.group(2) + m.group(1), text)


#: Les machines à écrire sans touche « 1 » : le chiffre un tapé « I » dans les dates
#: (« COTONOU, le I3 Décembre I963 », « du I4 Août I965 », « le 3I Décembre »). Lot 1,
#: tranche T002 : la date de signature échappait au pipeline, qui retenait celle d'un
#: visa. Seulement dans une date complète avec son mois en toutes lettres.
_DATE_I_RE = re.compile(
    r"\b(?P<jour>[0-3I]?[0-9I])(?P<milieu>(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet"
    r"|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+)(?P<annee>[I1][9I][0-9I]{2}|20[0-9I]{2})\b",
    re.IGNORECASE,
)


_LE_DEVANT_DATE_RE = re.compile(
    r"(?<=,\s)[1I]e(?=\s+[0-3]?[0-9](?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t"
    r"|septembre|octobre|novembre|d[ée]cembre)\s+(?:19|20)\d\d\b)",
    re.IGNORECASE,
)


def fix_typewriter_one_in_dates(text: str) -> tuple[str, int]:
    """Rend le « 1 » tapé « I » dans les dates (voir ``_DATE_I_RE``)."""
    compte = [0]

    def rendre(m: re.Match[str]) -> str:
        jour, annee = m.group("jour"), m.group("annee")
        if "I" not in jour and "I" not in annee:
            return m.group(0)
        compte[0] += 1
        return jour.replace("I", "1") + m.group("milieu") + annee.replace("I", "1")

    text = _DATE_I_RE.sub(rendre, text)
    # « COTONOU, 1e 13 Décembre 1963 » : l'article « le » lu « 1e » devant une date.
    text, n = _LE_DEVANT_DATE_RE.subn("le", text)
    return text, compte[0] + n


def apply_ocr_fixes(text: str) -> tuple[str, int]:
    """Corrige les confusions OCR les plus sûres.

    Uniquement appliqué aux documents réellement OCRisés : sur un texte natif,
    ces motifs seraient au mieux inutiles, au pire destructeurs.
    """
    text, count = restore_decorative_o(text)
    text, n = fix_typewriter_one_in_dates(text)
    count += n
    for pattern, replacement in OCR_CONFUSION_RULES:
        text, substitutions = pattern.subn(replacement, text)
        count += substitutions
    return text, count


def is_page_number_line(line: str) -> bool:
    """Vrai si la ligne n'est qu'un numéro de page isolé."""
    stripped = line.strip()
    if not stripped or len(stripped) > 20:
        return False
    if is_protected(stripped):
        return False
    if PAGE_NUMBER_RE.match(stripped):
        return True
    # Romains : on exige l'absence de lettre isolée ambiguë (« I », « V », « X »
    # peuvent être des débuts de titre) — au moins deux caractères.
    compact = stripped.strip("-–—()[] ")
    return bool(ROMAN_PAGE_RE.match(stripped)) and len(compact) >= 2


# ---------------------------------------------------------------------------
# En-têtes et pieds de page répétés
# ---------------------------------------------------------------------------


def _normalize_for_comparison(line: str) -> str:
    """Forme canonique servant à repérer une ligne répétée d'une page à l'autre.

    Les chiffres sont neutralisés afin que « Journal officiel — page 12 » et
    « Journal officiel — page 13 » soient reconnus comme le même en-tête.
    """
    text = unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode()
    text = re.sub(r"\d+", "#", text.casefold())
    return re.sub(r"[^a-z#]+", " ", text).strip()


def effective_zone(line_count: int, zone_lines: int) -> int:
    """Profondeur réelle de la zone d'en-tête/pied pour une page donnée.

    Les zones haute et basse ne doivent **jamais** se recouvrir : sur une page
    courte, une zone de 3 lignes engloberait le corps du texte, qui pourrait
    alors être supprimé comme un en-tête répété. On la plafonne donc à la
    moitié de la page.
    """
    return max(1, min(zone_lines, line_count // 2))


def detect_repeated_lines(
    pages_lines: Sequence[Sequence[str]],
    zone_lines: int = 3,
    min_ratio: float = 0.55,
    position: str = "top",
) -> set[str]:
    """Repère les lignes répétées en tête (ou en pied) des pages.

    Args:
        pages_lines: lignes de chaque page, dans l'ordre.
        zone_lines: profondeur maximale de la zone examinée.
        min_ratio: proportion de pages où la ligne doit apparaître.
        position: ``"top"`` ou ``"bottom"``.

    Returns:
        Les formes canoniques à supprimer. Vide si le document a moins de trois
        pages : sans répétition observable, on ne suppose rien.
    """
    page_count = len(pages_lines)
    if page_count < 3:
        return set()

    counter: Counter[str] = Counter()
    for lines in pages_lines:
        depth = effective_zone(len(lines), zone_lines)
        zone = lines[:depth] if position == "top" else lines[-depth:]
        seen: set[str] = set()
        for line in zone:
            stripped = line.strip()
            if not stripped or is_protected(stripped) or len(stripped) < 4:
                continue
            canonical = _normalize_for_comparison(stripped)
            if canonical and canonical not in seen:
                counter[canonical] += 1
                seen.add(canonical)

    threshold = max(3, int(page_count * min_ratio))
    return {canonical for canonical, count in counter.items() if count >= threshold}


# ---------------------------------------------------------------------------
# Nettoyage d'une page
# ---------------------------------------------------------------------------


def clean_page_text(
    text: str,
    config: Config,
    repeated_top: Iterable[str] = (),
    repeated_bottom: Iterable[str] = (),
    is_ocr: bool = False,
    report: CleaningReport | None = None,
) -> str:
    """Nettoie le texte d'une page.

    Args:
        text: texte brut de la page.
        config: section ``cleaning`` de la configuration.
        repeated_top / repeated_bottom: formes canoniques d'en-têtes et pieds
            de page identifiées à l'échelle du document.
        is_ocr: active les corrections spécifiques à l'OCR.
        report: journal alimenté au fil des suppressions.

    Returns:
        Le texte nettoyé. Un texte vide en entrée ressort vide.
    """
    report = report or CleaningReport()
    if not text:
        return ""

    section = config.section("cleaning")

    if section.get("strip_control_chars", True):
        text, removed = strip_control_chars(text)
        report.control_chars_removed += removed
    if section.get("normalize_unicode", True):
        text = normalize_unicode(text)
    if section.get("strip_reader_markup", True):
        # Avant tout le reste : les règles suivantes (articles, dates, OCR)
        # attendent du texte, pas « $ \underline{\text{Article 2}} $ ».
        text, markup = strip_reader_markup(text)
        report.reader_markup_removed += markup
    if section.get("ocr_confusion_fixes", True):
        # Volontairement **sans** condition sur ``is_ocr``. Un PDF peut arriver
        # avec une couche texte produite par l'OCR de quelqu'un d'autre : le
        # pipeline le classe « natif », alors que son texte porte toutes les
        # confusions d'un scan. Deux codes béninois l'ont montré — « Arlicle »
        # y apparaît 75 fois dans un document réputé natif, et autant
        # d'articles disparaissaient.
        #
        # Ce qui compte n'est pas qui a produit l'OCR, mais si le texte en
        # porte les traces. Les règles sont assez étroites pour ne rien
        # changer à un texte sain : sur un document propre, le compteur
        # ``ocr_corrections`` reste simplement à zéro.
        text, corrections = apply_ocr_fixes(text)
        report.ocr_corrections += corrections
        if corrections and not is_ocr:
            report.native_text_repaired += corrections
    if section.get("fix_hyphenation", True):
        text, joined = fix_hyphenation(text)
        report.joined_hyphenations += joined
    if section.get("rejoin_article_headers", True):
        # Appliqué **avant** la suppression des numéros de page : un « 88 »
        # isolé, seconde ligne d'un en-tête éclaté, serait sinon confondu avec
        # une pagination et supprimé.
        text, rejoined = rejoin_split_article_headers(text)
        report.rejoined_article_headers += rejoined

    top = set(repeated_top)
    bottom = set(repeated_bottom)
    zone = int(section.get("header_footer_zone_lines", 3))
    protect = bool(section.get("protect_legal_content", True))
    drop_numbers = bool(section.get("remove_page_numbers", True))
    drop_headers = bool(section.get("remove_repeated_headers", True))
    drop_footers = bool(section.get("remove_repeated_footers", True))

    lines = text.split("\n")
    depth = effective_zone(len(lines), zone)
    kept: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue

        # Veto : une ligne juridique n'est jamais supprimée.
        if protect and is_protected(stripped):
            report.protected_lines_kept += 1
            kept.append(line)
            continue

        in_top = index < depth
        in_bottom = index >= len(lines) - depth
        canonical = _normalize_for_comparison(stripped)

        if drop_headers and in_top and canonical in top:
            report.removed_headers.append(stripped)
            continue
        if drop_footers and in_bottom and canonical in bottom:
            report.removed_footers.append(stripped)
            continue
        if drop_numbers and (in_top or in_bottom) and is_page_number_line(stripped):
            report.removed_page_numbers += 1
            continue
        if DECORATIVE_RE.match(stripped):
            report.removed_decorative_lines += 1
            continue

        kept.append(line)

    text = "\n".join(kept)

    if section.get("join_wrapped_lines", True):
        text, joined = join_wrapped_lines(text)
        report.joined_wrapped_lines += joined
    if section.get("collapse_whitespace", True):
        text = collapse_whitespace(text)

    return text


# ---------------------------------------------------------------------------
# Nettoyage d'un document
# ---------------------------------------------------------------------------


def clean_pages(
    pages: Sequence[Page],
    config: Config,
    document_id: str = "",
) -> tuple[list[Page], CleaningReport]:
    """Nettoie toutes les pages d'un document.

    Le repérage des en-têtes et pieds de page se fait à l'échelle du document :
    une ligne n'est considérée comme répétitive que si elle apparaît sur une
    proportion suffisante de pages.

    Les pages renvoyées sont de **nouveaux** objets : ``raw_text`` conserve le
    texte d'origine, ``text`` reçoit la version nettoyée.
    """
    report = CleaningReport(document_id=document_id or (pages[0].document_id if pages else ""))
    if not pages:
        return [], report

    section = config.section("cleaning")
    zone = int(section.get("header_footer_zone_lines", 3))
    min_ratio = float(section.get("header_footer_min_ratio", 0.55))

    report.pages_processed = len(pages)
    report.chars_before = sum(len(page.text) for page in pages)

    # Pré-normalisation légère, uniquement pour comparer les lignes entre pages.
    pages_lines = [normalize_unicode(page.text).split("\n") for page in pages]
    repeated_top = (
        detect_repeated_lines(pages_lines, zone, min_ratio, "top")
        if section.get("remove_repeated_headers", True)
        else set()
    )
    repeated_bottom = (
        detect_repeated_lines(pages_lines, zone, min_ratio, "bottom")
        if section.get("remove_repeated_footers", True)
        else set()
    )

    cleaned: list[Page] = []
    for page in pages:
        is_ocr = str(getattr(page.method, "value", page.method)) == "ocr"
        text = clean_page_text(
            page.text,
            config,
            repeated_top=repeated_top,
            repeated_bottom=repeated_bottom,
            is_ocr=is_ocr,
            report=report,
        )
        new_page = Page(
            document_id=page.document_id,
            page=page.page,
            text=text,
            source_file=page.source_file,
            char_count=len(text),
            raw_text=page.raw_text if page.raw_text is not None else page.text,
            method=page.method,
            ocr_confidence=page.ocr_confidence,
            warnings=list(page.warnings),
        )
        if page.text.strip() and not text.strip():
            new_page.warnings.append("page_videe_par_le_nettoyage")
            report.warnings.append(
                f"page {page.page} : entièrement vidée par le nettoyage — à vérifier"
            )
        cleaned.append(new_page)

    report.chars_after = sum(len(page.text) for page in cleaned)
    report.removed_headers = sorted(set(report.removed_headers))
    report.removed_footers = sorted(set(report.removed_footers))

    # Garde-fou global : une suppression massive est presque toujours un bug.
    if report.removal_ratio > 0.25:
        report.warnings.append(
            f"{report.removal_ratio:.0%} du texte supprimé par le nettoyage — "
            "vérification humaine fortement recommandée"
        )
        logger.warning(
            "%s : nettoyage agressif (%.0f%% du texte retiré)",
            report.document_id,
            report.removal_ratio * 100,
        )

    logger.info(
        "%s : nettoyage de %d page(s), %d caractères retirés "
        "(%d en-tête(s), %d pied(s), %d numéro(s) de page)",
        report.document_id,
        report.pages_processed,
        report.chars_removed,
        len(report.removed_headers),
        len(report.removed_footers),
        report.removed_page_numbers,
    )
    return cleaned, report
