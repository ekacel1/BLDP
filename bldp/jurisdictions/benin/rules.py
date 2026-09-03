"""Règles propres à la République du Bénin.

Ce module ne redéclare rien de ce que le socle générique sait déjà faire : il
ajoute uniquement les formes rencontrées dans les textes béninois — références
au Journal Officiel, autorités émettrices nationales, formules de promulgation,
numérotation officielle ``AAAA-NNN``.

Pour ajouter le Togo ou la Côte d'Ivoire, copier ce fichier dans
``bldp/jurisdictions/<pays>/rules.py`` et adapter les motifs : le cœur du
pipeline n'a pas à changer (§29).
"""

from __future__ import annotations

import re

from bldp.core.parser.rules import NUMBER, RuleSet, StructureRule
from bldp.jurisdictions.registry import JurisdictionProfile
from bldp.utils import DASH_CLASS, DASHES, NUMERO_PREFIX, OCR_DIGIT
from bldp.models import StructureLevel

_FLAGS = re.IGNORECASE | re.UNICODE


# ---------------------------------------------------------------------------
# Parsing : formes d'en-têtes spécifiques
# ---------------------------------------------------------------------------

#: « Article 45 nouveau » — forme employée dans les textes modificatifs
#: béninois pour désigner la rédaction issue d'une loi modificative.
ARTICLE_NOUVEAU = StructureRule(
    level=StructureLevel.ARTICLE,
    pattern=re.compile(
        rf"^\s*(?:article|art\.)\s*{NUMBER}\s*"
        r"(?P<mention>nouveau|nouvelle|bis|ter)\s*[:.\-–—]?\s*(?P<heading>.*)$",
        _FLAGS,
    ),
    priority=75,   # prioritaire sur la règle générique d'article
    name="benin_article_nouveau",
    max_line_length=400,
    content_is_body=True,
)

#: « Article unique » — fréquent dans les décrets courts.
ARTICLE_UNIQUE = StructureRule(
    level=StructureLevel.ARTICLE,
    pattern=re.compile(
        r"^\s*(?:article|art\.)\s+(?P<number>unique)\s*[:.\-–—]?\s*(?P<heading>.*)$",
        _FLAGS,
    ),
    priority=76,
    name="benin_article_unique",
    max_line_length=400,
    content_is_body=True,
)

#: Fin de la partie normative : formules de promulgation béninoises.
BENIN_STOP_PATTERNS = [
    re.compile(r"^\s*fait\s+[àa]\s+cotonou", _FLAGS),
    re.compile(r"^\s*fait\s+[àa]\s+porto[-\s]novo", _FLAGS),
    # Accents optionnels : l'OCR les perd fréquemment sur les scans anciens.
    # Cette formule n'agit comme clôture que si elle n'est pas elle-même un
    # article numéroté — au Bénin, elle l'est presque toujours.
    re.compile(
        r"la\s+pr[ée]sente\s+loi\s+sera\s+ex[ée]cut[ée]e\s+comme\s+loi\s+de\s+l['’]?[ée]tat",
        _FLAGS,
    ),
    re.compile(r"^\s*par\s+le\s+pr[ée]sident\s+de\s+la\s+r[ée]publique", _FLAGS),
]

#: Lignes de sommaire propres aux publications officielles.
BENIN_TOC_PATTERNS = [
    re.compile(r"^\s*sommaire\s+du\s+journal\s+officiel", _FLAGS),
]


# ---------------------------------------------------------------------------
# Métadonnées : types, autorités, numéros, dates
# ---------------------------------------------------------------------------

#: Type de document, reconnu sur les premières pages.
#:
#: **L'ordre des entrées fait foi** : le premier type dont un motif correspond
#: l'emporte. Les formes les plus spécifiques doivent donc précéder les plus
#: larges — une décision de la Cour constitutionnelle relève de la
#: jurisprudence, et non du type générique « décision », dont le motif est
#: volontairement placé en dernier.
DOCUMENT_TYPE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    # « portant Constitution de la République du Bénin » désigne **toujours** la
    # loi 90-32, citée dans les visas de presque tous les textes béninois. Le
    # motif est donc ancré en début de ligne : seule une véritable Constitution
    # s'intitule ainsi. Sans cet ancrage, 13 documents sur 24 étaient typés
    # « constitution » à cause de leurs visas.
    "constitution": [
        re.compile(
            r"^\s*constitution\s+de\s+la\s+r[ée]publique\s+du\s+b[ée]nin\b",
            _FLAGS | re.MULTILINE,
        )
    ],
    "loi": [
        re.compile(rf"\bloi\s+(?:organique\s+){{0,1}}{NUMERO_PREFIX}{OCR_DIGIT}{{4}}", _FLAGS),
        re.compile(rf"^\s*loi\s+{NUMERO_PREFIX}", _FLAGS | re.MULTILINE),
    ],
    "code": [
        re.compile(r"\bcode\s+(?:du|de|des|d['’])\s*\w+", _FLAGS),
        re.compile(r"^\s*code\s+", _FLAGS | re.MULTILINE),
    ],
    # « DÉGRET », « DEGRET » : l'OCR confond régulièrement C et G sur ces scans.
    "decret": [re.compile(rf"\bd[ée][cgq]ret\s*{NUMERO_PREFIX}{OCR_DIGIT}{{4}}", _FLAGS)],
    "arrete": [
        re.compile(
            rf"\barr[êeé]t[ée]\s+(?:interminist[ée]riel\s+)?{NUMERO_PREFIX}", _FLAGS
        )
    ],
    "ordonnance": [re.compile(rf"\bordonnance\s*{NUMERO_PREFIX}", _FLAGS)],
    "circulaire": [re.compile(rf"\bcirculaire\s*{NUMERO_PREFIX}", _FLAGS)],
    # Émanant d'une juridiction : jurisprudence, quelle que soit l'appellation
    # de l'acte (arrêt, décision DCC...).
    # La juridiction doit être l'**émetteur**, pas une mention au fil du texte.
    # « proclamation par la Cour constitutionnelle des résultats » figure dans
    # le corps de nombreux décrets : sans ancrage en début de ligne, ils étaient
    # typés « jurisprudence ».
    "jurisprudence": [
        re.compile(
            r"^\s*(?:cour\s+(?:constitutionnelle|supr[êe]me|d['’]appel)"
            r"|tribunal\s+(?:de\s+premi[èe]re\s+instance|de\s+commerce))\b",
            _FLAGS | re.MULTILINE,
        ),
        re.compile(rf"\barr[êe]t\s*{NUMERO_PREFIX}{OCR_DIGIT}", _FLAGS),
        re.compile(r"\bd[ée]cision\s+dcc\b", _FLAGS),
    ],
    # Repli le plus large : à ne consulter qu'après tous les types précédents.
    "decision": [re.compile(r"\bd[ée]cision\s+n\s*[°ºo]\s*\d", _FLAGS)],
}

#: Autorité émettrice.
AUTHORITY_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Assemblée nationale": [re.compile(r"\bassembl[ée]e\s+nationale\b", _FLAGS)],
    "Président de la République": [
        re.compile(r"\bpr[ée]sident\s+de\s+la\s+r[ée]publique\b", _FLAGS)
    ],
    "Cour constitutionnelle": [re.compile(r"\bcour\s+constitutionnelle\b", _FLAGS)],
    "Cour suprême": [re.compile(r"\bcour\s+supr[êe]me\b", _FLAGS)],
    "Conseil des ministres": [re.compile(r"\bconseil\s+des\s+ministres\b", _FLAGS)],
    "Secrétariat Général du Gouvernement": [
        re.compile(r"\bsecr[ée]tariat\s+g[ée]n[ée]ral\s+du\s+gouvernement\b", _FLAGS),
        re.compile(r"\bSGG\b"),
    ],
}

#: Autorite emettrice qu'implique le type de l'acte, en droit beninois.
#:
#: On ne retient ici que les cas ou l'institution ne laisse aucune place au
#: doute : la loi est votee par l'Assemblee nationale, le decret et
#: l'ordonnance sont pris par le President de la Republique. Un code est une
#: loi, il suit la meme regle.
#:
#: Sont volontairement absents les arretes (ministre, prefet, maire selon le
#: cas), les decisions et les accords : leur autorite varie, et la deduire
#: serait inventer. Mieux vaut un champ vide et signale qu'un champ rempli et
#: faux.
#:
#: Mesure sur le lot 1 du corpus SGG : 842 documents sans autorite lisible,
#: dont la quasi-totalite sont des lois et des ordonnances.
DEFAULT_AUTHORITY_BY_TYPE: dict[str, str] = {
    "loi": "Assemblée nationale",
    "code": "Assemblée nationale",
    "decret": "Président de la République",
    "ordonnance": "Président de la République",
}

#: Séparateur d'un numéro officiel : tout tiret Unicode, l'underscore ou le
#: point, seuls ou combinés (« 2010.- 028 » se rencontre sur des scans bruités).
_NUM_SEP = f"[{re.escape(DASHES + '_.')}]{{1,3}}"

#: Le numéro ne doit pas se terminer **au milieu d'un mot**.
#:
#: Sans ce garde-fou, « N"2olo.- Oü8 » — où l'OCR a détruit le « 2 » de 028 —
#: produisait « 2010-0 » avec 0,92 de confiance : un numéro faux, annoncé comme
#: sûr. Le refus de capturer renvoie le document vers la validation humaine,
#: ce qui est le comportement voulu (§33).
_NOT_TRUNCATED = r"(?!\w)"

#: Numéro officiel : « n° 2026-313 », « N° 2020-113/PR/MTFP ».
#:
#: Deux tolérances tirées de scans réels : le symbole « ° » que l'OCR rend par
#: une apostrophe ou un guillemet (:data:`NUMERO_PREFIX`), et les lettres lues
#: à la place de chiffres (:data:`OCR_DIGIT`). Sans elles, aucun numéro n'était
#: reconnu — et sans numéro, la date et le type dérivaient vers les visas.
NUMBER_PATTERNS = [
    # Millésime + série : « n° 2026-313 », « N" 2019 _ 230 », « N'2010-01 ».
    re.compile(
        rf"{NUMERO_PREFIX}(?P<number>{OCR_DIGIT}{{4}}\s*{_NUM_SEP}\s*{OCR_DIGIT}{{1,4}}"
        rf"(?:\s*/\s*[A-Z0-9./-]+)?){_NOT_TRUNCATED}",
        _FLAGS,
    ),
    # Forme courte : « n° 90-32 ».
    re.compile(
        rf"{NUMERO_PREFIX}(?P<number>{OCR_DIGIT}{{1,4}}\s*{_NUM_SEP}\s*{OCR_DIGIT}{{2,4}})"
        rf"{_NOT_TRUNCATED}",
        _FLAGS,
    ),
]

#: Date de signature : « du 10 février 2026 ».
DATE_PATTERNS = [
    re.compile(
        r"\bdu\s+(?P<day>\d{1,2})(?:er)?\s+(?P<month>janvier|f[ée]vrier|mars|avril|mai|juin|"
        r"juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+(?P<year>\d{4})\b",
        _FLAGS,
    ),
    re.compile(
        r"\b(?P<day>\d{1,2})(?:er)?\s+(?P<month>janvier|f[ée]vrier|mars|avril|mai|juin|"
        r"juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+(?P<year>\d{4})\b",
        _FLAGS,
    ),
    re.compile(r"\b(?P<day>\d{1,2})[/-](?P<month_num>\d{1,2})[/-](?P<year>\d{4})\b"),
]

#: Sources officielles connues (métadonnée ``source``).
OFFICIAL_SOURCES = {
    "SGG": "Secrétariat Général du Gouvernement",
    "JORB": "Journal Officiel de la République du Bénin",
    "AN": "Assemblée nationale du Bénin",
    "COUR_CONST": "Cour constitutionnelle du Bénin",
}

#: Statut juridique déclaré dans le texte lui-même.
STATUS_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "abroge": [
        re.compile(r"\best\s+abrog[ée]e?\b", _FLAGS),
        re.compile(r"\bsont\s+abrog[ée]e?s\b", _FLAGS),
    ],
    "modifie": [
        re.compile(r"\best\s+modifi[ée]e?\b", _FLAGS),
        re.compile(r"\bmodifiant\s+(?:et\s+compl[ée]tant\s+)?la\s+loi\b", _FLAGS),
    ],
    "remplace": [re.compile(r"\bremplac[ée]e?\s+par\b", _FLAGS)],
}

#: Relations entre textes (§13). Le groupe ``target`` capture la référence citée.
RELATION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "abroge": [
        re.compile(
            r"\babroge(?:nt)?\s+(?:les?\s+dispositions\s+de\s+)?"
            r"(?P<target>(?:la\s+)?loi\s+n\s*[°ºo]\s*[\d\s\-–/]+"
            r"|(?:le\s+)?d[ée]cret\s+n\s*[°ºo]\s*[\d\s\-–/]+)",
            _FLAGS,
        ),
    ],
    "abroge_partiellement": [
        re.compile(
            r"\babroge(?:nt)?\s+partiellement\s+(?P<target>[^.;]{5,120})", _FLAGS
        ),
        re.compile(
            r"\bl['’]?article\s+\d+\s+de\s+(?P<target>(?:la\s+)?loi\s+n\s*[°ºo]\s*[\d\s\-–/]+)"
            r"\s+est\s+abrog[ée]",
            _FLAGS,
        ),
    ],
    "modifie": [
        re.compile(
            r"\bmodifi(?:e|ant|ent)\s+(?:et\s+compl[ée]t\w+\s+)?"
            r"(?P<target>(?:la\s+)?loi\s+n\s*[°ºo]\s*[\d\s\-–/]+"
            r"|(?:le\s+)?d[ée]cret\s+n\s*[°ºo]\s*[\d\s\-–/]+"
            r"|(?:le\s+)?code\s+\w+(?:\s+\w+){0,3})",
            _FLAGS,
        ),
    ],
    "remplace": [
        re.compile(
            r"\bremplace(?:nt)?\s+(?P<target>(?:la\s+)?loi\s+n\s*[°ºo]\s*[\d\s\-–/]+"
            r"|(?:le\s+)?d[ée]cret\s+n\s*[°ºo]\s*[\d\s\-–/]+)",
            _FLAGS,
        ),
    ],
    "applique": [
        re.compile(
            r"\b(?:pour\s+l['’]application\s+de|en\s+application\s+de)\s+"
            r"(?P<target>(?:la\s+)?loi\s+n\s*[°ºo]\s*[\d\s\-–/]+)",
            _FLAGS,
        ),
    ],
    "cite": [
        re.compile(
            r"\bvu\s+(?P<target>(?:la\s+)?loi\s+n\s*[°ºo]\s*[\d\s\-–/]+"
            r"|(?:le\s+)?d[ée]cret\s+n\s*[°ºo]\s*[\d\s\-–/]+)",
            _FLAGS,
        ),
    ],
}


def build() -> JurisdictionProfile:
    """Profil de la juridiction béninoise.

    Le registre fusionne automatiquement ces règles par-dessus le socle
    générique : seules les spécificités locales figurent ici.
    """
    return JurisdictionProfile(
        name="benin",
        display_name="République du Bénin",
        language="fr",
        ruleset=RuleSet(
            name="benin",
            structure_rules=[],
            article_rules=[ARTICLE_UNIQUE, ARTICLE_NOUVEAU],
            stop_patterns=BENIN_STOP_PATTERNS,
            toc_patterns=BENIN_TOC_PATTERNS,
        ),
        document_type_patterns=DOCUMENT_TYPE_PATTERNS,
        authority_patterns=AUTHORITY_PATTERNS,
        default_authority_by_type=DEFAULT_AUTHORITY_BY_TYPE,
        number_patterns=NUMBER_PATTERNS,
        date_patterns=DATE_PATTERNS,
        official_sources=OFFICIAL_SOURCES,
        status_patterns=STATUS_PATTERNS,
        relation_patterns=RELATION_PATTERNS,
    )
