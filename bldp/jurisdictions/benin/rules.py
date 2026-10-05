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
    # Forme numérique, espaces admis autour des séparateurs : l'ordonnance
    # 93-04/PCS-CAB de la Cour suprême porte « du 10 - 03 - 1993 », que la
    # forme stricte « 10-03-1993 » ne voyait pas (essai du 5 octobre 2026).
    re.compile(r"\b(?P<day>\d{1,2})\s*[/.-]\s*(?P<month_num>\d{1,2})\s*[/.-]\s*(?P<year>\d{4})\b"),
]

#: Sources officielles connues (métadonnée ``source``).
OFFICIAL_SOURCES = {
    "SGG": "Secrétariat Général du Gouvernement",
    "JORB": "Journal Officiel de la République du Bénin",
    "AN": "Assemblée nationale du Bénin",
    "COUR_CONST": "Cour constitutionnelle du Bénin",
}

#: Statut juridique déclaré dans le texte lui-même : **aucun motif**.
#:
#: Ce dictionnaire était rempli, et c'était une erreur de raisonnement. Il
#: cherchait « est abrogé », « sont abrogées », « remplacé par » dans le texte
#: d'un document et en concluait que **ce document** était abrogé. Or ces
#: formules disent presque toujours l'inverse, ou tout autre chose :
#:
#:   « Le présent décret **qui abroge** toutes dispositions antérieures »
#:        → ce décret abroge ; il n'est pas abrogé. Clause de style présente
#:          dans 881 documents sur 4 000 sondés.
#:   « il peut être **remplacé par** un agent de son choix »
#:        → il s'agit d'un fonctionnaire, pas du décret.
#:   « le tableau de l'article 6 du décret 10/PCM/MF est **remplacé par**… »
#:        → c'est ce texte-ci qui remplace, dans un *autre* texte.
#:
#: Mesuré sur le corpus : 1 119 documents portaient un statut posé ainsi, à une
#: confiance de 0,50. C'est le mécanisme du bug des numéros — un motif trouvé
#: dans le texte, attribué au document qui le contient au lieu du document
#: qu'il vise.
#:
#: Le statut d'un texte se déduit des relations **entrantes** : c'est à cela
#: que sert ``STATUS_FROM_INCOMING`` dans ``bldp.core.relations``. Un document
#: ne peut pas savoir qu'il sera un jour abrogé ; seul le texte abrogeant le
#: sait, et c'est lui qui le dit. Laisser ce dictionnaire vide n'est donc pas
#: une capacité perdue, c'est la suppression d'une source d'affirmations
#: fausses — et « un texte sans signal reste inconnu » (§13).
STATUS_PATTERNS: dict[str, list[re.Pattern[str]]] = {}


# ---------------------------------------------------------------------------
# Relations entre textes : le fragment de référence, partagé
# ---------------------------------------------------------------------------

#: Les types de texte que le corpus contient **réellement**. S'en tenir à
#: « loi » et « décret », comme le faisaient les motifs d'origine, laissait de
#: côté 1 395 ordonnances, 580 codes, 445 décisions et 56 constitutions.
_TYPE_CITE = (
    r"(?:lois?|d[ée]crets?|ordonnances?|arr[êe]t[ée]s?|codes?|constitutions?"
    r"|d[ée]cisions?|accords?|conventions?)"
)

#: Une référence citée : un type de texte, puis un numéro.
#:
#: ``NUMERO_PREFIX`` est le « n° » tolérant à l'OCR écrit pour la réparation
#: des numéros. Il n'avait jamais été appliqué ici, et c'est **la** raison du
#: silence : les motifs d'origine exigeaient le caractère ``°`` littéral, quand
#: le corpus porte « décret n" 98-625 », « loi n' 86-014 », « BCRET ne 240 ».
#: Sur 4 000 documents sondés, les motifs stricts trouvaient 7 relations à
#: conséquence juridique ; en levant cette seule contrainte et les deux
#: suivantes, on en trouve 461.
#:
#: Le numéro est **obligatoire**, et c'est lui qui protège de la clause de
#: style : « abroge toutes dispositions antérieures contraires » ne nomme aucun
#: texte, ne porte aucun numéro, et ne produit donc aucune relation.
#: Remplissage « tempéré » : chaque caractère avalé doit ne PAS ouvrir un
#: nouveau nom de texte, et ne jamais franchir une fin de ligne.
#:
#: Sans cette précaution, un remplissage bête enjambe une citation entière.
#: Relevé sur decret_1963_118 :
#:
#:     texte    « VU la Constitution du Dahomeyl ⏎ VU la loi n° 59-2l »
#:     capturé  « la Constitution du Dahomeyl VU la loi n° 59-2l »
#:     produit  « constitution n° 59-002 »
#:
#: Le **type** d'une citation collé au **numéro** de la suivante : une
#: référence qui n'existe pas, et qui aurait été résolue vers un vrai document.
#: Un faux de cette nature est plus grave qu'un oubli.
def _liaison(budget: int) -> str:
    return rf"(?:(?!{_TYPE_CITE})[^.;\n]){{0,{budget}}}?"


_CIBLE = (
    r"(?:(?:la|le|les|l['’])\s*)?" + _TYPE_CITE +
    r"(?:\s+organique|\s+constitutionnelle|\s+de\s+finances)?"
    + _liaison(20) + NUMERO_PREFIX +
    rf"{OCR_DIGIT}{{1,4}}\s*[{re.escape(DASHES)}_/]\s*{OCR_DIGIT}{{1,4}}"
    r"(?:\s*/\s*[A-Z][\w./\-]{0,24})?"
)


#: Le complément d'agent : « … par la loi n° 2019-40 ».
#:
#: C'est le piège de l'inversion, et il a failli passer. « X **modifiée par**
#: la loi n° 2019-40 » ne dit pas que le document modifie la loi 2019-40 : il
#: dit que la loi 2019-40 modifie X. Enregistrer la relation dans ce sens la
#: retourne — c'est précisément l'erreur, en sens inverse, qui avait fait
#: marquer « abrogé » 476 documents qui abrogeaient.
#:
#: Et le lien correct n'est pas représentable ici : dans « Vu la loi n° 98-004,
#: modifiée par la loi n° 2019-40 », ni l'une ni l'autre n'est le document
#: courant, alors que ``source_document_id`` est toujours ce document. Face à
#: un lien qu'on ne peut pas écrire juste, on n'écrit rien.
_AGENT = (r"\s+par\s+(?:(?:la|le|les|l['’])\s*)?" + _TYPE_CITE)


def _cite_apres(verbe: str, sauf: str = "") -> re.Pattern[str]:
    """« abroge le décret n° X » — la cible **suit** le verbe, et le subit.

    C'est la seule construction que voyaient les motifs d'origine. ``verbe`` est
    donné en racine (« abrog »), de sorte que « abroge », « abrogeant » et le
    substantif « abrogation » — « portant abrogation du décret n° X », très
    fréquent dans les intitulés — soient couverts d'un seul motif.

    Le complément d'agent est exclu : après « abrogé **par** », ce qui suit est
    l'auteur de l'abrogation, pas sa victime.
    """
    # Les gardes sont placés AVANT « \w* », jamais après : sinon le moteur
    # recule d'un caractère (« abrog|é par » au lieu de « abrogé| par »), la
    # négation devient vraie et l'exclusion ne sert plus à rien. Un test le
    # vérifie sur « le décret n° 58-2 est abrogé par la loi n° 90-032 ».
    interdits = [rf"(?!\w*{_AGENT})"]
    if sauf:
        interdits.append(rf"(?!\w*\s+{sauf})")
    return re.compile(
        r"\b" + verbe + "".join(interdits) +
        r"\w*\s+(?:" + _liaison(40) + r"\s)?(?P<target>" + _CIBLE + r")",
        _FLAGS,
    )


def _cite_avant(verbe: str) -> re.Pattern[str]:
    """« le décret n° X **est abrogé** » — la cible **précède** le verbe.

    Deuxième restriction levée : un motif qui ne regarde qu'à droite du verbe
    ne voit jamais la tournure passive, pourtant la plus courante dans les
    articles d'abrogation béninois (« Sont et demeurent abrogées les
    dispositions du Décret n° 89-151 »).

    Le sens de la relation ne change pas — *tant qu'aucun agent n'est nommé*.
    « L'article 6 du décret n° X est remplacé par le suivant » : c'est bien le
    document courant qui remplace. Mais « le décret n° X est remplacé par la
    loi n° Y » désigne Y comme auteur, et la relation ne concerne alors plus le
    document courant du tout : elle est écartée.
    """
    return re.compile(
        r"(?P<target>" + _CIBLE + r")" + _liaison(60) + r"\b"
        r"(?:est|sont|demeure|demeurent)\s+(?:et\s+\w+\s+)?" + verbe +
        rf"(?!\w*{_AGENT})\w*",   # garde avant « \w* » — cf. _cite_apres
        _FLAGS,
    )

#: Relations entre textes (§13). Le groupe ``target`` capture la référence citée.
#:
#: Chaque relation à conséquence juridique est cherchée dans les **deux sens** :
#: la cible après le verbe (« abroge le décret n° X ») et avant lui
#: (« le décret n° X est abrogé »). ``needs_review`` reste vrai dans tous les
#: cas : élargir la détection augmente aussi les faux positifs, et c'est le
#: prix symétrique de la restriction qu'on lève. Ce que ces motifs produisent
#: est une **piste à vérifier**, jamais une affirmation.
RELATION_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    # « partiellement » est exclu ici pour ne pas produire deux relations
    # concurrentes sur la même phrase : la portée partielle a son propre type.
    "abroge": [
        _cite_apres("abrog", sauf="partiellement"),
        _cite_avant("abrog"),
    ],
    "abroge_partiellement": [
        re.compile(
            r"\babroge(?:nt)?\s+partiellement\s+(?P<target>[^.;]{5,120})", _FLAGS
        ),
        re.compile(
            r"\bl['’]?article\s+\d+\s+de\s+(?P<target>" + _CIBLE + r")"
            r"[^.;]{0,40}?\s+est\s+abrog[ée]",
            _FLAGS,
        ),
    ],
    "modifie": [
        _cite_apres("modifi"),
        _cite_avant("modifi"),
    ],
    "remplace": [
        _cite_apres("remplac"),
        _cite_avant("remplac"),
    ],
    "complete": [
        _cite_apres("compl[ée]t"),
    ],
    "applique": [
        re.compile(
            r"\b(?:pour\s+l['’]application\s+de|en\s+application\s+de|"
            r"pris\s+en\s+application\s+de)\s+(?:[^.;]{0,30}?\s)?"
            r"(?P<target>" + _CIBLE + r")",
            _FLAGS,
        ),
    ],
    # « Vu … » : le préambule, la forme la plus régulière et la plus sûre.
    # Elle portait déjà 22 946 des 23 099 relations ; elle est ici étendue aux
    # mêmes types de texte et au même « n° » tolérant que le reste.
    "cite": [
        re.compile(r"\bvu\s+(?P<target>" + _CIBLE + r")", _FLAGS),
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
