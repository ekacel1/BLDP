"""Corrections proposées par la relecture, et ce qui les autorise.

Ce module est le **garde-fou**, et c'est la pièce importante du sous-système.

La relecture est une **collation** : on met l'image de la page d'origine en
regard de la transcription produite par la chaîne, et on relève les écarts. Ce
que le modèle doit reporter, c'est ce qui est écrit sur l'image — pas ce qui
devrait logiquement s'y trouver. Un modèle de langue est excellent pour deviner
la suite d'une phrase ; sur un corpus juridique, cette qualité est un défaut.
Un article « rétabli » de mémoire est un faux, et un faux plausible est pire
qu'un texte visiblement abîmé : personne n'ira le vérifier.

D'où le principe : **aucune correction n'est appliquée sur la seule parole du
modèle.** Chacune doit franchir une vérification mécanique, faite ici.

Toute la difficulté tient à ce que la référence — l'image — n'est pas
mécaniquement lisible par ce module. On ne peut donc pas *prouver* qu'une
lecture est juste ; on peut en revanche **borner** ce qu'une lecture a le droit
d'être, et rejeter tout ce qui ressemble à une invention plutôt qu'à une
transcription.

Chaque correction déclare la nature de sa preuve, et les contrôles en
dépendent :

``preuve = texte_ocr``
    La valeur proposée doit se retrouver **dans le texte extrait**. C'est le
    régime strict : le modèle ne fait que réparer un mot abîmé dont la forme
    correcte figure ailleurs dans la page.

``preuve = image``
    Le modèle affirme lire sur l'image quelque chose que l'OCR n'a pas vu. On
    ne peut pas le vérifier directement, alors on vérifie tout le reste : que
    l'image a bien été fournie, que la page citée existe et porte l'article
    visé, que le texte reste une transcription du même passage et non un autre
    texte, et — pour un numéro — qu'il s'inscrit dans la suite de ses voisins.

Deux invariants ne dépendent d'aucune preuve : **rien ne disparaît** (aucune
correction ne vide un texte ni ne supprime un article) et **une relecture n'est
pas une réécriture** (au-delà d'une part du document, tout part en
signalement). Ce qui ne passe pas n'est jamais jeté : c'est retenu comme
*signalement*, et un humain tranche.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence

from bldp.logging_setup import get_logger

logger = get_logger("review.corrections")


#: Champs qu'une correction peut viser. Tout le reste est refusé d'emblée :
#: la liste est fermée pour qu'un modèle ne puisse pas inventer une cible.
CORRIGIBLE_FIELDS: frozenset[str] = frozenset(
    {"article_number", "article_text", "title", "number", "date"}
)

#: Natures de preuve qu'une correction peut invoquer.
#:
#: ``texte_ocr`` : la forme correcte figure ailleurs dans le texte extrait.
#: ``image`` : le modèle la lit sur l'image de la page d'origine.
EVIDENCE_SOURCES: frozenset[str] = frozenset({"image", "texte_ocr"})

#: Fidélité minimale d'un texte corrigé quand la preuve est le texte extrait.
#:
#: Régime des réparations légères : « Arlicle » → « Article » (0,86),
#: « 2018-OO1 » → « 2018-001 » (0,75), un recollage d'espaces (0,98). Une
#: phrase à laquelle on ajoute une proposition tombe à 0,66 et se fait
#: refuser.
MIN_TEXT_FIDELITY = 0.70

#: Le même seuil quand la preuve est l'image de la page.
#:
#: Ce seuil bas n'est pas un relâchement, c'est une mesure. Sur un scan très
#: abîmé — « F-rr appircalion des drsposilrons des artr#les » là où l'image
#: porte « En application des dispositions des articles » — une transcription
#: *exacte* ne dépasse pas 0,56 de fidélité. Un seuil calibré sur des dégâts
#: légers rejetterait donc précisément les documents qui ont le plus besoin
#: d'être relus. Mesuré sur le corpus : transcription exacte 0,56 ; invention
#: plausible 0,40 ; reformulation 0,35.
#:
#: Une transcription juste d'un texte encore plus dégradé peut passer sous ce
#: seuil et se faire refuser. C'est le sens de marche voulu : une correction
#: refusée devient un signalement qu'un humain lit, une correction appliquée à
#: tort ne se voit plus.
MIN_TEXT_FIDELITY_IMAGE = 0.50

#: Variation de longueur tolérée sur un texte corrigé.
MAX_LENGTH_DRIFT = 0.25

#: La même tolérance quand la preuve est l'image : un OCR peut avoir perdu
#: plusieurs mots d'une ligne mal imprimée.
MAX_LENGTH_DRIFT_IMAGE = 0.40

#: Écart maximal entre un numéro d'article corrigé et la place qu'il occupe
#: dans la suite de ses voisins.
#:
#: C'est le seul contrôle mécanique possible sur un numéro lu à l'image : si
#: l'article situé entre le 7 et le 9 est relu « 8 », la lecture s'inscrit dans
#: la suite ; relu « 42 », elle ne s'y inscrit pas, et cela se signale.
MAX_NUMBER_GAP = 2.0

#: Confiance en deçà de laquelle une correction n'est jamais appliquée,
#: même si elle franchit les autres contrôles.
MIN_CONFIDENCE = 0.80

#: Part maximale d'articles corrigés dans un document. Au-delà, ce n'est plus
#: une relecture, c'est une réécriture : tout part en signalement.
MAX_CORRECTED_SHARE = 0.60

#: Nombre d'articles en deçà duquel la part ci-dessus ne veut rien dire.
#:
#: Sur un décret à article unique, une seule correction légitime représente
#: 100 % du document et déclencherait un refus d'ensemble — le garde-fou
#: interdirait précisément ce qu'il est censé permettre. En dessous de ce
#: seuil, seules les vérifications individuelles s'appliquent ; elles font de
#: toute façon l'essentiel du travail.
MIN_ARTICLES_FOR_SHARE_GUARD = 4


@dataclass
class Correction:
    """Une correction proposée, et son sort après vérification."""

    field: str
    before: str
    after: str
    justification: str
    confidence: float = 0.0
    target_id: Optional[str] = None       # article_id visé, le cas échéant
    #: Nature de la preuve invoquée : ``image`` ou ``texte_ocr``.
    evidence_source: str = "texte_ocr"
    #: Page où la preuve se lit. Obligatoire pour une preuve par l'image.
    page: int = 0
    #: Renseignés par :func:`verify_correction`.
    accepted: bool = False
    refusal: str = ""

    @property
    def from_image(self) -> bool:
        return self.evidence_source == "image"

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "target_id": self.target_id,
            "before": self.before,
            "after": self.after,
            "justification": self.justification,
            "confidence": round(self.confidence, 3),
            "evidence_source": self.evidence_source,
            "page": self.page,
            "accepted": self.accepted,
            "refusal": self.refusal,
        }

    @property
    def summary(self) -> str:
        """Résumé d'une ligne, pour le journal de suivi."""
        cible = f" ({self.target_id})" if self.target_id else ""
        ou = f" [p. {self.page}, {self.evidence_source}]" if self.page else ""
        return (
            f"{self.field}{cible}{ou} : "
            f"{self.before[:40]!r} -> {self.after[:40]!r}"
        )


@dataclass
class ArticleRef:
    """Ce qu'on sait d'un article, du point de vue de la vérification."""

    article_id: str
    number: str
    numeric_value: Optional[float]
    page_start: int
    page_end: int
    position: int

    def covers(self, page: int) -> bool:
        return self.page_start <= page <= max(self.page_end, self.page_start)


@dataclass
class SourceContext:
    """Tout ce contre quoi une correction peut être confrontée.

    Le champ ``images_sent`` mérite un mot : il permet de refuser une
    correction qui invoque l'image alors qu'aucune image n'a été transmise.
    Un modèle qui justifie une lecture par une référence qu'il n'a pas reçue
    ne lit pas — il compose, et c'est précisément ce qu'on cherche à arrêter.
    """

    text: str = ""
    articles: list[ArticleRef] = field(default_factory=list)
    pages: frozenset[int] = frozenset()
    images_sent: bool = False

    @classmethod
    def from_text(cls, text: str) -> "SourceContext":
        """Contexte minimal : le texte extrait, sans image ni structure."""
        return cls(text=text)

    @classmethod
    def from_document(cls, document, images_sent: bool = False) -> "SourceContext":
        from bldp.utils import parse_number

        return cls(
            text="\n".join(page.text for page in document.pages),
            articles=[
                ArticleRef(
                    article_id=article.article_id,
                    number=article.article_number,
                    # Recalculée si elle manque : un document venu d'une base
                    # plus ancienne ne doit pas désactiver silencieusement le
                    # contrôle de cohérence des numéros.
                    numeric_value=(
                        article.numeric_value
                        if article.numeric_value is not None
                        else parse_number(article.article_number)
                    ),
                    page_start=article.page_start,
                    page_end=article.page_end,
                    position=article.position,
                )
                for article in document.articles
            ],
            pages=frozenset(page.page for page in document.pages),
            images_sent=images_sent,
        )

    def article(self, article_id: Optional[str]) -> Optional[ArticleRef]:
        if not article_id:
            return None
        return next(
            (a for a in self.articles if a.article_id == article_id), None
        )

    def neighbours(self, article_id: str) -> tuple[Optional[float], Optional[float]]:
        """Valeurs numériques des articles qui encadrent celui-ci."""
        ordonnes = sorted(self.articles, key=lambda a: a.position)
        index = next(
            (i for i, a in enumerate(ordonnes) if a.article_id == article_id), None
        )
        if index is None:
            return None, None
        avant = ordonnes[index - 1].numeric_value if index > 0 else None
        apres = (
            ordonnes[index + 1].numeric_value
            if index + 1 < len(ordonnes)
            else None
        )
        return avant, apres


@dataclass
class Finding:
    """Un problème constaté mais non corrigeable automatiquement."""

    code: str
    message: str
    severity: str = "warning"
    target_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "target_id": self.target_id,
        }


# ---------------------------------------------------------------------------
# Comparaisons
# ---------------------------------------------------------------------------


def letters_only(text: str) -> str:
    """Suite des lettres et des chiffres, accents et ponctuation retirés.

    C'est l'invariant du nettoyage d'OCR : corriger un texte scanné change la
    ponctuation, les espaces et les accents, presque jamais cette suite-là.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(
        c for c in decomposed if c.isalnum() and not unicodedata.combining(c)
    )


def letter_similarity(before: str, after: str) -> float:
    """Similarité des suites de lettres, entre 0 et 1."""
    a, b = letters_only(before), letters_only(after)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


#: Taille des n-grammes de caractères comparés.
NGRAM_SIZE = 4


def _normalized(text: str) -> str:
    """Texte réduit à ses lettres, chiffres et espaces simples."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    sans_accents = "".join(
        c for c in decomposed if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", sans_accents)).strip()


def ngram_overlap(before: str, after: str, size: int = NGRAM_SIZE) -> float:
    """Part de matière commune, mesurée en n-grammes de caractères.

    Insensible à l'endroit des erreurs, là où une comparaison séquentielle
    s'effondre : un caractère faux tous les cinq caractères casse tous les
    blocs communs, alors que la matière, elle, reste largement partagée.
    """
    a, b = _normalized(before), _normalized(after)
    if len(a) < size or len(b) < size:
        return 0.0
    ga = Counter(a[i:i + size] for i in range(len(a) - size + 1))
    gb = Counter(b[i:i + size] for i in range(len(b) - size + 1))
    communs = sum((ga & gb).values())
    return 2 * communs / (sum(ga.values()) + sum(gb.values()))


def text_fidelity(before: str, after: str) -> float:
    """À quel point le texte proposé reste le même texte, entre 0 et 1.

    Deux mesures, parce qu'aucune ne couvre seule les deux régimes d'erreur.

    La comparaison séquentielle juge bien les réparations locales — un mot
    dont deux lettres changent — mais s'effondre sur un scan très abîmé : une
    transcription pourtant exacte y tombe à 0,26, parce qu'il ne subsiste plus
    aucun bloc commun de longueur utile.

    Le chevauchement de n-grammes juge bien ce second cas, où la matière reste
    partagée même sans blocs contigus, mais il est aveugle aux chaînes courtes
    (« Arlicle » et « Article » n'ont qu'un 4-gramme commun sur huit).

    On retient donc la plus favorable des deux : chacune fait autorité là où
    elle voit clair, et aucune ne condamne un texte que l'autre reconnaît.
    """
    return max(letter_similarity(before, after), ngram_overlap(before, after))


def appears_in_source(value: str, source: str) -> bool:
    """Vrai si ``value`` se retrouve dans ``source``, à la mise en forme près."""
    cible = letters_only(value)
    return bool(cible) and cible in letters_only(source)


def article_header_in_source(number: str, source: str) -> bool:
    """Vrai si un en-tête « Article <number> » figure dans le texte source.

    Un numéro corrigé doit venir de la page, jamais d'une déduction. Le motif
    reste tolérant à l'OCR — c'est justement du texte abîmé qu'on relit.
    """
    numero = re.escape(number.strip())
    motif = re.compile(
        rf"\bar\w{{0,3}}[il1]?[cd]?[lt1]?e?\s*[.,]?\s*{numero}\b",
        re.IGNORECASE,
    )
    return bool(motif.search(source))


# ---------------------------------------------------------------------------
# Vérification
# ---------------------------------------------------------------------------


def _as_context(source: "str | SourceContext") -> SourceContext:
    return source if isinstance(source, SourceContext) else SourceContext.from_text(source)


def verify_correction(
    correction: Correction, source: "str | SourceContext"
) -> Correction:
    """Décide si une correction peut être appliquée, et note pourquoi.

    Args:
        correction: la proposition du modèle.
        source: le contexte de vérification — texte extrait, articles, pages,
            et le fait que l'image ait été fournie ou non. Une simple chaîne
            est acceptée : elle vaut « texte extrait, sans image ».

    Returns:
        La correction, avec ``accepted`` et ``refusal`` renseignés. Une
        correction refusée n'est pas perdue : elle devient un signalement.
    """
    contexte = _as_context(source)
    correction.accepted = False

    if correction.field not in CORRIGIBLE_FIELDS:
        correction.refusal = f"champ non corrigeable : {correction.field}"
        return correction

    if not correction.after.strip():
        correction.refusal = "une correction ne peut pas vider une valeur"
        return correction

    if correction.before.strip() == correction.after.strip():
        correction.refusal = "sans effet"
        return correction

    if correction.confidence < MIN_CONFIDENCE:
        correction.refusal = (
            f"confiance {correction.confidence:.2f} < {MIN_CONFIDENCE:.2f}"
        )
        return correction

    if not correction.justification.strip():
        correction.refusal = "aucune justification fournie"
        return correction

    motif = _verify_evidence(correction, contexte)
    if motif:
        correction.refusal = motif
        return correction

    verificateur = {
        "article_text": _verify_text,
        "article_number": _verify_article_number,
        "number": _verify_value_in_source,
        "title": _verify_text_in_source,
        "date": _verify_date,
    }[correction.field]

    motif = verificateur(correction, contexte)
    if motif:
        correction.refusal = motif
        return correction

    correction.accepted = True
    return correction


def _verify_evidence(correction: Correction, contexte: SourceContext) -> str:
    """Contrôle la preuve invoquée avant de regarder la valeur elle-même."""
    if correction.evidence_source not in EVIDENCE_SOURCES:
        return (
            f"nature de preuve inconnue : {correction.evidence_source!r} "
            f"(attendu : {', '.join(sorted(EVIDENCE_SOURCES))})"
        )

    if not correction.from_image:
        return ""

    # À partir d'ici, le modèle affirme lire sur l'image.
    if not contexte.images_sent:
        return (
            "la correction invoque l'image de la page, mais aucune image n'a "
            "été transmise : cette lecture n'a pas eu lieu"
        )
    if not correction.page:
        return "une lecture sur l'image doit citer la page où elle se lit"
    if contexte.pages and correction.page not in contexte.pages:
        return (
            f"la page {correction.page} citée n'existe pas dans ce document"
        )

    article = contexte.article(correction.target_id)
    if article is not None and not article.covers(correction.page):
        return (
            f"la page {correction.page} citée ne porte pas l'article visé "
            f"(il est page {article.page_start})"
        )
    return ""


def _verify_text(correction: Correction, contexte: SourceContext) -> str:
    """Un texte corrigé reste une transcription du même passage.

    Le contrôle de longueur mérite un mot : il refuse aussi bien un ajout
    inventé qu'une ligne réellement manquée par l'OCR et rétablie à juste
    titre. Les deux sont mécaniquement indiscernables — même profil, même
    ampleur. Le refus est donc le bon comportement dans les deux cas : la
    proposition part en signalement, avec son texte, et un humain tranche.
    Rétablir une ligne entière est précisément une décision qui lui revient.
    """
    seuil = MIN_TEXT_FIDELITY_IMAGE if correction.from_image else MIN_TEXT_FIDELITY
    fidelite = text_fidelity(correction.before, correction.after)
    if fidelite < seuil:
        return (
            f"le texte a trop changé (fidélité {fidelite:.2f} < {seuil:.2f}) "
            "— relire n'est pas réécrire"
        )

    limite = MAX_LENGTH_DRIFT_IMAGE if correction.from_image else MAX_LENGTH_DRIFT
    avant, apres = len(correction.before), len(correction.after)
    if avant and abs(apres - avant) / avant > limite:
        return (
            f"la longueur varie de {100 * (apres - avant) / avant:+.0f} % "
            f"(limite {100 * limite:.0f} %) — à confirmer par un humain"
        )
    return ""


def _verify_text_in_source(correction: Correction, contexte: SourceContext) -> str:
    """Un titre corrigé doit se lire quelque part — dans le texte ou sur l'image."""
    if correction.from_image:
        return ""
    if not appears_in_source(correction.after, contexte.text):
        return "la valeur proposée ne figure pas dans le texte du document"
    return ""


def _verify_article_number(correction: Correction, contexte: SourceContext) -> str:
    """Un numéro corrigé se lit ; il ne se déduit jamais.

    Deux régimes. Sans image, la seule preuve recevable est un en-tête
    « Article N » réellement présent dans le texte extrait. Avec l'image, on
    ne peut pas vérifier la lecture — on vérifie alors qu'elle s'inscrit dans
    la suite de ses voisins, ce qui écarte les lectures fantaisistes sans
    prétendre confirmer les autres.
    """
    if not correction.from_image:
        if not article_header_in_source(correction.after, contexte.text):
            return (
                f"aucun en-tête « Article {correction.after} » dans le texte "
                "extrait : sans l'image, un numéro ne se déduit pas"
            )
        return ""

    from bldp.utils import parse_number

    valeur = parse_number(correction.after)
    if valeur is None:
        # Un numéro non numérique (« premier », « unique ») n'a pas de place
        # dans une suite : on s'en remet aux autres contrôles.
        return ""

    avant, apres = contexte.neighbours(correction.target_id or "")
    attendus = [v for v in (avant, apres) if v is not None]
    if not attendus:
        return ""

    ecart = min(abs(valeur - voisin) for voisin in attendus)
    if ecart > MAX_NUMBER_GAP:
        voisinage = " / ".join(f"{v:g}" for v in attendus)
        return (
            f"le numéro {correction.after} ne s'inscrit pas dans la suite "
            f"(voisins : {voisinage}) — à faire confirmer par un humain"
        )
    return ""


def _verify_value_in_source(correction: Correction, contexte: SourceContext) -> str:
    if correction.from_image:
        return ""
    if not appears_in_source(correction.after, contexte.text):
        return "le numéro proposé ne figure pas dans le texte du document"
    return ""


def _verify_date(correction: Correction, contexte: SourceContext) -> str:
    """Une date corrigée doit être lisible, jour et année.

    On vérifie les composantes plutôt que la chaîne ISO : le document écrit
    « 11 mai 2022 », jamais « 2022-05-11 ». Le format reste exigé même quand
    la preuve est l'image — c'est la forme attendue en sortie, pas une preuve.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", correction.after.strip()):
        return "une date corrigée doit être au format AAAA-MM-JJ"
    if correction.from_image:
        return ""

    annee, _, jour = correction.after.split("-")
    plat = letters_only(contexte.text)
    if annee not in plat:
        return f"l'année {annee} ne figure pas dans le texte extrait"
    if jour.lstrip("0") and jour.lstrip("0") not in plat and jour not in plat:
        return f"le jour {jour} ne figure pas dans le texte extrait"
    return ""


def verify_all(
    corrections: Sequence[Correction],
    source: "str | SourceContext",
    article_count: int = 0,
) -> tuple[list[Correction], list[Finding]]:
    """Vérifie un lot de corrections et sépare le bon grain de l'ivraie.

    Un garde-fou d'ensemble s'ajoute aux contrôles individuels : si le modèle
    veut corriger la majorité des articles, ce n'est plus une relecture mais
    une réécriture, et rien n'est appliqué. Le doute joue contre la machine.

    Returns:
        ``(acceptées, signalements)`` — chaque refus devient un signalement,
        de sorte qu'aucune observation ne se perd.
    """
    contexte = _as_context(source)
    article_count = article_count or len(contexte.articles)
    verifiees = [verify_correction(c, contexte) for c in corrections]
    acceptees = [c for c in verifiees if c.accepted]

    touches = {c.target_id for c in acceptees if c.field == "article_text"}
    if (
        article_count >= MIN_ARTICLES_FOR_SHARE_GUARD
        and len(touches) > MAX_CORRECTED_SHARE * article_count
    ):
        logger.warning(
            "Relecture IA : %d article(s) sur %d seraient réécrits — refus en bloc.",
            len(touches), article_count,
        )
        for correction in acceptees:
            correction.accepted = False
            correction.refusal = (
                f"refus d'ensemble : {len(touches)} articles sur {article_count} "
                "corrigés, ce n'est plus une relecture"
            )
        acceptees = []

    # Un refus doit rester exploitable : le relecteur humain a besoin de la
    # lecture proposée, pas seulement de savoir qu'il y en avait une. C'est
    # souvent lui qui tranchera, et il ne rouvrira pas le rapport JSON pour
    # aller chercher le texte.
    signalements = [
        Finding(
            code="correction_refusee",
            message=(
                f"{c.summary} — refusée : {c.refusal}. "
                f"Lecture proposée ({c.evidence_source}"
                + (f", p. {c.page}" if c.page else "")
                + f", confiance {c.confidence:.2f}) : {c.after[:400]!r}"
            ),
            severity="info",
            target_id=c.target_id,
        )
        for c in verifiees
        if not c.accepted and c.refusal != "sans effet"
    ]
    return acceptees, signalements
