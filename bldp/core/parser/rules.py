"""Règles de détection des structures juridiques — définitions génériques.

Ce module ne contient **aucune** particularité béninoise : il définit le
vocabulaire de règles (:class:`StructureRule`, :class:`RuleSet`) et un jeu de
règles pour le français juridique en général. Les adaptations propres à une
juridiction vivent dans ``bldp/jurisdictions/<pays>/`` (§29).

Une règle est essentiellement une expression régulière ancrée en début de ligne,
associée à un niveau hiérarchique. Elle expose deux groupes nommés facultatifs :

``number``
    le numéro de la subdivision (« II », « 3 », « premier ») ;
``heading``
    l'intitulé qui suit éventuellement sur la même ligne.

Les règles sont conçues pour être **prudentes** : mieux vaut ne pas détecter une
subdivision douteuse — le contrôle qualité la signalera — que découper un
article au mauvais endroit et corrompre le corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Pattern

from bldp.models import StructureLevel

# ---------------------------------------------------------------------------
# Briques de motifs réutilisables
# ---------------------------------------------------------------------------

#: Ordinaux littéraux, utilisés seuls (« Titre premier ») ou avant le mot-clé
#: (« PREMIÈRE PARTIE »).
WORD_ORDINAL = (
    r"premier|première|premiere"
    r"|deuxième|deuxieme|second[e]?|troisième|troisieme|quatrième|quatrieme"
    r"|cinquième|cinquieme|sixième|sixieme|septième|septieme|huitième|huitieme"
    r"|neuvième|neuvieme|dixième|dixieme"
)

#: Marque ordinale accolée à un chiffre : « 1er », « 1 er », « 2ème ».
#: ``\b`` empêche d'avaler le « e » de « est » dans « Article 45 est abrogé ».
_ORDINAL_MARK = r"(?:\s*(?:er|ère|ere|ème|eme|e)\b)?"

#: Suffixe latin d'insertion : « 45 bis », « 45 ter ».
_LATIN_SUFFIX = (
    r"(?:\s*(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies)\b)?"
)

#: Numéro de subdivision : arabe (avec ordinal, composé et suffixe), romain, ou
#: littéral. L'ordre des alternatives compte : la forme la plus longue d'abord.
NUMBER = (
    r"(?P<number>"
    rf"\d{{1,4}}(?:\s*[-–]\s*\d{{1,4}})?{_ORDINAL_MARK}{_LATIN_SUFFIX}"
    r"|[IVXLCDM]{1,7}"                  # romain
    rf"|(?:{WORD_ORDINAL})"
    r")"
)

#: Séparateur entre le numéro et l'intitulé : « : », « . », « - » ou rien.
SEPARATOR = r"(?:\s*[:.\-–—]\s*|\s+|$)"

#: Intitulé optionnel qui suit le numéro sur la même ligne.
HEADING = r"(?P<heading>.*)"


def heading_pattern(keyword: str, number_required: bool = True) -> str:
    """Construit le motif d'un en-tête de subdivision.

    Args:
        keyword: alternative de mots-clés, ex. ``"chapitre"`` ou ``"titre|tit\\."``.
        number_required: si faux, le mot-clé seul suffit (« ANNEXE »).
    """
    number_part = rf"\s+{NUMBER}" if number_required else rf"(?:\s+{NUMBER})?"
    return rf"^\s*(?:{keyword}){number_part}{SEPARATOR}{HEADING}$"


# ---------------------------------------------------------------------------
# Règle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructureRule:
    """Règle de reconnaissance d'un en-tête hiérarchique.

    Attributes:
        level: niveau hiérarchique produit.
        pattern: expression régulière ancrée sur une ligne complète.
        priority: en cas d'ambiguïté, la règle de plus faible valeur gagne.
        requires_uppercase_line: n'accepter que les lignes en majuscules, utile
            pour les documents où seules les subdivisions sont capitalisées.
        max_line_length: au-delà, la ligne est du texte courant, pas un titre.
        name: identifiant lisible, utile au débogage et aux journaux.
    """

    level: StructureLevel
    pattern: Pattern[str]
    priority: int = 100
    requires_uppercase_line: bool = False
    max_line_length: int = 200
    name: str = ""

    def match(self, line: str) -> Optional[re.Match[str]]:
        """Teste la règle sur une ligne déjà débarrassée de ses espaces."""
        if not line or len(line) > self.max_line_length:
            return None
        if self.requires_uppercase_line and not _is_upper_line(line):
            return None
        return self.pattern.match(line)


def _is_upper_line(line: str) -> bool:
    """Vrai si la ligne est essentiellement en capitales."""
    letters = [c for c in line if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= 0.85


@dataclass
class RuleSet:
    """Ensemble de règles applicable à une juridiction ou un type de document.

    Attributes:
        name: identifiant du jeu de règles (« generic », « benin »...).
        structure_rules: règles de subdivisions (titre, chapitre, section...).
        article_rules: règles de détection des articles.
        alinea_pattern: motif d'un alinéa numéroté en début de ligne.
        stop_patterns: motifs marquant la fin de la zone normative (signatures,
            « Fait à ... le ... ») — au-delà, on cesse d'ouvrir des articles.
        toc_patterns: motifs de sommaire, à ignorer pour éviter de créer des
            articles fantômes à partir de la table des matières.
    """

    name: str = "generic"
    structure_rules: list[StructureRule] = field(default_factory=list)
    article_rules: list[StructureRule] = field(default_factory=list)
    alinea_pattern: Optional[Pattern[str]] = None
    stop_patterns: list[Pattern[str]] = field(default_factory=list)
    toc_patterns: list[Pattern[str]] = field(default_factory=list)

    def all_rules(self) -> list[StructureRule]:
        """Toutes les règles, triées par priorité croissante."""
        return sorted(
            [*self.structure_rules, *self.article_rules], key=lambda rule: rule.priority
        )

    def match_line(self, line: str) -> Optional[tuple[StructureRule, re.Match[str]]]:
        """Renvoie la première règle qui reconnaît la ligne, par priorité."""
        for rule in self.all_rules():
            found = rule.match(line)
            if found:
                return rule, found
        return None

    def is_stop_line(self, line: str) -> bool:
        """Vrai si la ligne marque la fin de la partie normative."""
        return any(pattern.search(line) for pattern in self.stop_patterns)

    def is_toc_line(self, line: str) -> bool:
        """Vrai si la ligne appartient visiblement à un sommaire."""
        return any(pattern.search(line) for pattern in self.toc_patterns)

    def extend(self, other: "RuleSet") -> "RuleSet":
        """Fusionne un jeu de règles spécialisé par-dessus celui-ci.

        Les règles du jeu spécialisé sont ajoutées avec leur propre priorité :
        c'est elle, et non l'ordre de fusion, qui décide en cas d'ambiguïté.
        Cela permet à une juridiction d'ajouter des formes locales sans
        réécrire le socle générique.
        """
        return RuleSet(
            name=other.name,
            structure_rules=[*self.structure_rules, *other.structure_rules],
            article_rules=[*self.article_rules, *other.article_rules],
            alinea_pattern=other.alinea_pattern or self.alinea_pattern,
            stop_patterns=[*self.stop_patterns, *other.stop_patterns],
            toc_patterns=[*self.toc_patterns, *other.toc_patterns],
        )


# ---------------------------------------------------------------------------
# Jeu de règles générique (français juridique)
# ---------------------------------------------------------------------------

_FLAGS = re.IGNORECASE | re.UNICODE


def _rule(
    level: StructureLevel,
    keyword: str,
    priority: int,
    name: str,
    number_required: bool = True,
) -> StructureRule:
    return StructureRule(
        level=level,
        pattern=re.compile(heading_pattern(keyword, number_required), _FLAGS),
        priority=priority,
        name=name,
    )


#: Certaines subdivisions placent l'ordinal **avant** le mot-clé :
#: « PREMIÈRE PARTIE », « DEUXIÈME PARTIE ». Motif inversé dédié.
PARTIE_INVERSEE = StructureRule(
    level=StructureLevel.PARTIE,
    pattern=re.compile(
        rf"^\s*(?P<number>{WORD_ORDINAL}|\d{{1,2}}\s*(?:ère|ere|ème|eme|e)?)\s+"
        rf"partie{SEPARATOR}{HEADING}$",
        re.IGNORECASE | re.UNICODE,
    ),
    priority=9,
    name="partie_inversee",
)

#: Règles de subdivisions, de la plus englobante à la plus fine.
GENERIC_STRUCTURE_RULES: list[StructureRule] = [
    PARTIE_INVERSEE,
    _rule(StructureLevel.PARTIE, r"partie", 10, "partie"),
    _rule(StructureLevel.LIVRE, r"livre", 20, "livre"),
    _rule(StructureLevel.SOUS_TITRE, r"sous[-\s]?titre", 25, "sous_titre"),
    _rule(StructureLevel.TITRE, r"titre", 30, "titre"),
    _rule(StructureLevel.CHAPITRE, r"chapitre|chap\.", 40, "chapitre"),
    _rule(StructureLevel.SOUS_SECTION, r"sous[-\s]?section", 45, "sous_section"),
    _rule(StructureLevel.SECTION, r"section", 50, "section"),
    _rule(StructureLevel.PARAGRAPHE, r"paragraphe|§", 60, "paragraphe"),
    _rule(StructureLevel.ANNEXE, r"annexes?", 70, "annexe", number_required=False),
]

#: Règles d'articles. Le §24 impose de reconnaître au minimum :
#: « Article 1 », « Article 1er », « Article premier », « Art. 1 ».
GENERIC_ARTICLE_RULES: list[StructureRule] = [
    StructureRule(
        level=StructureLevel.ARTICLE,
        pattern=re.compile(
            rf"^\s*(?:article|art\.|art)\s*{NUMBER}{SEPARATOR}{HEADING}$", _FLAGS
        ),
        priority=80,
        name="article",
        max_line_length=400,
    ),
    # Article en tête de ligne suivi immédiatement du texte, sans séparateur :
    # « Article 45 Le salarié... ». Motif distinct pour rester lisible.
    StructureRule(
        level=StructureLevel.ARTICLE,
        pattern=re.compile(
            rf"^\s*(?:ARTICLE|ART\.)\s*{NUMBER}\s*(?P<heading>[A-ZÀ-Ü].*)$", re.UNICODE
        ),
        priority=85,
        name="article_majuscules",
        max_line_length=400,
    ),
]

#: Alinéa numéroté : « 1° », « 2) », « a) », « - ».
GENERIC_ALINEA_PATTERN = re.compile(
    r"^\s*(?P<number>\d{1,3}\s*[°)\-.]|[a-z]\s*[)\-.]|[-–—•])\s+(?=\S)", re.UNICODE
)

#: Fin de la partie normative : formules de signature et de promulgation.
#: Les accents sont rendus optionnels : l'OCR les perd fréquemment, et une
#: formule de signature non reconnue ferait rentrer des mentions parasites
#: (« Article 99 » d'un tampon, d'une annexe non normative) dans le corpus.
GENERIC_STOP_PATTERNS = [
    re.compile(r"^\s*fait\s+[àa]\s+.{2,40}\s*,?\s*le\s+", _FLAGS),
    re.compile(
        r"^\s*(?:ainsi\s+fait|par\s+le\s+pr[ée]sident"
        r"|le\s+pr[ée]sident\s+de\s+la\s+r[ée]publique)",
        _FLAGS,
    ),
]

#: Lignes de sommaire : « Article 5 .......... 12 ».
GENERIC_TOC_PATTERNS = [
    re.compile(r"\.{4,}\s*\d{1,4}\s*$"),
    re.compile(r"^\s*(?:sommaire|table\s+des\s+mati[èe]res)\s*$", _FLAGS),
]


def generic_ruleset() -> RuleSet:
    """Jeu de règles par défaut, valable pour le français juridique courant."""
    return RuleSet(
        name="generic",
        structure_rules=list(GENERIC_STRUCTURE_RULES),
        article_rules=list(GENERIC_ARTICLE_RULES),
        alinea_pattern=GENERIC_ALINEA_PATTERN,
        stop_patterns=list(GENERIC_STOP_PATTERNS),
        toc_patterns=list(GENERIC_TOC_PATTERNS),
    )


def compile_rules(
    specs: Iterable[dict],
    default_priority: int = 100,
) -> list[StructureRule]:
    """Compile des règles décrites en configuration (YAML).

    Chaque entrée accepte : ``level``, ``pattern``, ``priority``,
    ``uppercase_only``, ``max_line_length``, ``name``. Cela permet d'ajouter
    une forme locale sans écrire de Python.
    """
    compiled: list[StructureRule] = []
    for index, spec in enumerate(specs):
        level = StructureLevel(str(spec["level"]).lower())
        compiled.append(
            StructureRule(
                level=level,
                pattern=re.compile(spec["pattern"], _FLAGS),
                priority=int(spec.get("priority", default_priority + index)),
                requires_uppercase_line=bool(spec.get("uppercase_only", False)),
                max_line_length=int(spec.get("max_line_length", 200)),
                name=str(spec.get("name", f"custom_{index}")),
            )
        )
    return compiled
