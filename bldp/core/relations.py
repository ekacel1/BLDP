"""Module 8 — statut juridique, versions et relations entre textes (§13).

Un texte juridique vit : il est modifié, abrogé, remplacé. Ce module repère les
liens que les documents déclarent entre eux (« la présente loi abroge la loi
n° 2015-018 »), tente de résoudre la cible dans le corpus, puis en déduit le
statut des textes visés.

Le cahier des charges est explicite : *« pour le MVP, la détection peut être
semi-automatique. Le système doit signaler les relations qu'il n'est pas
suffisamment sûr de déterminer. »* D'où trois règles de conduite :

1. une relation dont la cible n'est pas résolue reste enregistrée, avec sa
   citation brute et ``needs_review=True`` — on ne jette pas l'information ;
2. le statut d'un texte n'est **jamais** modifié sur la foi d'une relation
   incertaine : en dessous du seuil de confiance, on signale sans agir ;
3. un texte sans signal reste ``inconnu``, jamais ``en_vigueur`` — supposer
   qu'un texte est en vigueur serait une affirmation juridique non fondée.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Sequence
from urllib.parse import unquote

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import (
    Document,
    DocumentType,
    LegalRelation,
    LegalStatus,
    RelationType,
)
from bldp.jurisdictions.registry import JurisdictionProfile, get_profile
from bldp.utils import official_number_key, same_official_number, slugify

logger = get_logger("relations")

#: Statut induit chez la **cible** d'une relation entrante.
STATUS_FROM_INCOMING: dict[RelationType, LegalStatus] = {
    RelationType.ABROGE: LegalStatus.ABROGE,
    RelationType.ABROGE_PARTIELLEMENT: LegalStatus.PARTIELLEMENT_ABROGE,
    RelationType.MODIFIE: LegalStatus.MODIFIE,
    RelationType.REMPLACE: LegalStatus.REMPLACE,
}

#: Gravité d'un statut : un texte abrogé ne « redevient » pas simplement modifié.
_STATUS_SEVERITY: dict[LegalStatus, int] = {
    LegalStatus.INCONNU: 0,
    LegalStatus.EN_VIGUEUR: 1,
    LegalStatus.MODIFIE: 2,
    LegalStatus.PARTIELLEMENT_ABROGE: 3,
    LegalStatus.REMPLACE: 4,
    LegalStatus.ABROGE: 5,
}

#: Référence normalisée : « loi n° 2026-001 » -> ``("loi", "2026-001")``.
_REFERENCE_RE = re.compile(
    r"(?P<kind>loi|d[ée]cret|arr[êe]t[ée]|ordonnance|code|constitution)"
    r"[^0-9]{0,30}?(?P<number>\d{2,4}\s*[-–]\s*\d{1,4})",
    re.IGNORECASE,
)

#: Article visé par une modification : « l'article 12 de la loi n° … ».
_TARGET_ARTICLE_RE = re.compile(r"\bl['’]?article\s+(?P<article>\d{1,4}(?:\s*bis|\s*ter)?)",
                                re.IGNORECASE)


@dataclass
class RelationReport:
    """Bilan de la détection de relations sur un lot."""

    relations_found: int = 0
    resolved: int = 0
    unresolved: int = 0
    #: Dont : citations menant à un numéro disputé (comptées aussi dans
    #: ``unresolved``).
    ambiguous: int = 0
    statuses_updated: int = 0
    statuses_flagged: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "relations_found": self.relations_found,
            "resolved": self.resolved,
            "unresolved": self.unresolved,
            "ambiguous": self.ambiguous,
            "statuses_updated": self.statuses_updated,
            "statuses_flagged": self.statuses_flagged,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Normalisation des références
# ---------------------------------------------------------------------------


def normalize_reference(reference: str) -> Optional[tuple[str, str]]:
    """Réduit une citation à ``(type, numéro)``, ou ``None`` si illisible.

    ``"la loi n° 2015 - 018"`` → ``("loi", "2015-018")``. La normalisation est
    volontairement stricte : un rapprochement approximatif entre deux textes
    juridiques serait plus dangereux qu'utile.
    """
    match = _REFERENCE_RE.search(reference or "")
    if not match:
        return None

    kind = _canonical_kind(match.group("kind"))
    number = re.sub(r"\s+", "", match.group("number")).replace("–", "-")
    # « 2015-18 » et « 2015-018 » désignent le même texte : on cadre à 3 chiffres.
    year, _, serial = number.partition("-")
    if serial.isdigit():
        number = f"{year}-{int(serial):03d}"
    return kind, number


def _canonical_kind(raw: str) -> str:
    lowered = raw.lower()
    for canonical in ("loi", "decret", "arrete", "ordonnance", "code", "constitution"):
        if lowered.startswith(canonical[:4]):
            return canonical
    # Formes accentuées
    if lowered.startswith("déc") or lowered.startswith("dec"):
        return "decret"
    if lowered.startswith("arr"):
        return "arrete"
    return lowered


def document_reference_key(document: Document) -> Optional[tuple[str, str]]:
    """Clé ``(type, numéro)`` d'un document, pour résoudre les citations."""
    metadata = document.metadata
    if not metadata.number or metadata.type is DocumentType.INCONNU:
        return None
    number = re.sub(r"\s+", "", metadata.number).replace("–", "-")
    year, _, serial = number.partition("-")
    if serial.isdigit():
        number = f"{year}-{int(serial):03d}"
    return metadata.type.value, number


#: Référence portée par l'adresse d'un document au SGG :
#: « https://sgg.gouv.bj/doc/decret-2016-711/ » → ``decret``, ``2016-711``.
#: Le tiret peut y être encodé (« decret-2016%E2%80%93706 »).
_SOURCE_URL_REFERENCE_RE = re.compile(
    r"/doc/(?P<kind>[a-z]+)-(?P<number>\d{2,4}\s*[-–—]\s*\d{1,4}"
    r"(?:[-_](?:bis|ter|quater)\d*)?)/?$",
    re.IGNORECASE,
)

#: Bornes d'un millésime plausible : un numéro « 9073-62 » ou « 2208-222 » est
#: une coquille, de l'OCR ou du portail.
_MILLESIME_MIN = 1850


def official_reference(document: Document) -> Optional[tuple[str, str]]:
    """``(type, numéro)`` que le SGG attribue au document, d'après son adresse.

    Ce n'est pas une vérité : le portail commet aussi des coquilles. C'est un
    **second témoin**, indépendant de la lecture du texte — et c'est leur
    accord qui fait la confiance.
    """
    url = unquote(document.metadata.source_url or "").rstrip()
    match = _SOURCE_URL_REFERENCE_RE.search(url)
    if not match:
        return None
    number = re.sub(r"\s+", "", match.group("number"))
    number = re.sub(r"[–—]", "-", number)
    # Pas de ``_canonical_kind`` ici : il ne regarde que les premières
    # lettres, et ferait d'une « décision » un « décret ».
    kind = match.group("kind").lower()
    return _SLUG_KINDS.get(kind, kind), number


#: Catégories d'adresse SGG ramenées aux types de citation reconnus.
_SLUG_KINDS = {"decret": "decret", "decrets": "decret", "loi": "loi", "lois": "loi",
               "ordonnance": "ordonnance", "ordon": "ordonnance", "arrete": "arrete",
               "code": "code", "constitution": "constitution"}


def _plausible(number: str) -> bool:
    key = official_number_key(number)
    return key is not None and _MILLESIME_MIN <= key[0] <= datetime.now().year + 1


def _padded(number: str) -> str:
    """Forme d'index : « 2016-7 » → « 2016-007 », comme :func:`normalize_reference`."""
    number = re.sub(r"\s+", "", number).replace("–", "-")
    year, _, serial = number.partition("-")
    return f"{year}-{int(serial):03d}" if serial.isdigit() else number


def _forms(number: str) -> set[str]:
    """Les notations sous lesquelles un même numéro peut être cité.

    « 2018-002/PR/ » est cité « 2018-002 » ; « 1996-532 » est cité aussi bien
    « 96-532 » que « 1996-532 », et :func:`normalize_reference` garde le
    millésime tel qu'il est écrit. Les deux notations sont donc indexées.
    """
    forms = {_padded(number)}
    key = official_number_key(number)
    if key is not None:
        year, serial, _ = key
        forms.add(f"{year}-{serial:03d}")
        if 1960 <= year <= 1999:
            forms.add(f"{year % 100:02d}-{serial:03d}")
    return forms


#: Types que produit :func:`normalize_reference`, donc sous lesquels un texte
#: peut être cité. Les catégories « accord » et « decision » du SGG n'en sont
#: pas — et elles mentent : sur le lot 1, 13 des 14 documents publiés sous ces
#: adresses sont des décrets.
_CITABLE_KINDS = frozenset({"loi", "decret", "ordonnance", "arrete", "code", "constitution"})


@dataclass(frozen=True)
class ReferenceClaim:
    """Une clé qu'un document revendique, et ce qui l'appuie."""

    key: tuple[str, str]
    #: Témoins concordants, de 0 à 2 : le type vient de l'adresse SGG (et non
    #: d'une lecture), le numéro lu est confirmé par l'adresse.
    strength: int
    #: Notation exacte du numéro du document, et non une notation dérivée
    #: (« 96-532 » pour un document numéroté « 1996-532 »).
    primary: bool


def reference_claims(document: Document) -> list[ReferenceClaim]:
    """Les clés ``(type, numéro)`` sous lesquelles on peut citer ce document.

    Deux témoins : ce qui est **lu dans le texte**, et **l'adresse SGG**.

    Le **type** est celui de l'adresse quand elle en donne un qui se cite
    (« loi », « décret »…) : la numérotation est propre à chaque type, et une
    ordonnance que la lecture a prise pour un décret disputerait son numéro au
    vrai décret. Le type lu ne sert que pour les adresses « accord » ou
    « decision », qui hébergent des décrets.

    Le **numéro** : quand les deux témoins s'accordent — notation mise à part,
    « 96-532 » et « 1996-532 » —, toutes les notations sont indexées. Quand ils
    divergent, le décret 2016-711 lu « 2016-292 » parce qu'il cite le 2016-292
    en est l'exemple réel : indexé sous le numéro lu, il captait les citations
    du vrai décret 2016-292 (mesure du 3 octobre 2026 : 569 liens sur 42 508
    menaient à un texte d'un autre numéro). Si l'un des deux numéros n'est pas
    plausible (« 9073-62 »), l'autre est retenu. Sinon **aucun** : une citation
    non résolue est un manque visible ; une citation rattachée au mauvais texte
    est une erreur invisible.
    """
    lu = document_reference_key(document)
    officiel = official_reference(document)
    numero_lu = document.metadata.number
    if officiel is None:
        if not lu:
            return []
        return [ReferenceClaim((lu[0], form), 0, form == lu[1]) for form in _forms(numero_lu)]

    url_kind, url_number = officiel
    if url_kind in _CITABLE_KINDS:
        kinds, kind_strength = {url_kind}, 1
    elif document.metadata.type not in (DocumentType.INCONNU, DocumentType.AUTRE):
        kinds, kind_strength = {document.metadata.type.value}, 0
    else:
        kinds, kind_strength = {url_kind}, 0

    if not numero_lu:
        forms, number_strength, primary = _forms(url_number), 0, _padded(url_number)
    elif same_official_number(numero_lu, url_number):
        forms, number_strength, primary = _forms(url_number) | _forms(numero_lu), 1, _padded(numero_lu)
    elif _plausible(url_number) and not _plausible(numero_lu):
        forms, number_strength, primary = _forms(url_number), 0, _padded(url_number)
        if official_number_key(numero_lu) is None:
            # Illisible plutôt que faux (« 076-26 ») : les textes qui le citent
            # portent souvent la même graphie. On garde aussi cette notation.
            forms = forms | {_padded(numero_lu)}
    elif _plausible(numero_lu) and not _plausible(url_number):
        forms, number_strength, primary = _forms(numero_lu), 0, _padded(numero_lu)
    else:
        return []
    strength = kind_strength + number_strength
    return [ReferenceClaim((kind, form), strength, form == primary) for kind in kinds for form in forms]


def document_reference_keys(document: Document) -> set[tuple[str, str]]:
    """Les clés sous lesquelles on peut citer ce document (voir :func:`reference_claims`)."""
    return {claim.key for claim in reference_claims(document)}


#: Valeur d'index d'une clé que se disputent des documents sans parenté. Une
#: citation qui y mène n'est pas résolue : elle est signalée.
REFERENCE_AMBIGUE = "?ambigu"

#: Force prêtée à une entrée d'un index externe dont on ignore la provenance :
#: un seul témoin.
_EXTERNAL_STRENGTH = 1

#: Suffixes de variante d'un identifiant : « bis », « ter », « quater ».
_VARIANT_RE = re.compile(r"_(?:bis|ter|quater)\d*$", re.IGNORECASE)


def common_root(document_id: str) -> str:
    """Racine d'un identifiant, variantes retirées, millésime cadré.

    « decret_1965_01 », « decret_1965_1 » et « decret_1965_1_bis » partagent la
    même racine : ce sont des éditions d'un même texte — le SGG publie
    légitimement « bis » et « ter » sous le même numéro. Règle reprise de
    ``garde_fou_index.py``, qui l'a établie sur le corpus réel.
    """
    bare = _VARIANT_RE.sub("", document_id)
    match = re.match(r"^([a-z]+)_(\d{2,4})_(\d{1,4})$", bare, re.IGNORECASE)
    if not match:
        return bare.lower()
    kind, year, serial = match.groups()
    return f"{kind.lower()}_{year[-2:]}_{int(serial):03d}"


class ReferenceIndex(dict):
    """Index ``(type, numéro) -> document_id`` qui ne laisse pas gagner le premier venu.

    Chaque attribution retient sa force (:class:`ReferenceClaim`). Quand deux
    documents revendiquent une même clé :

    * ce sont deux **éditions d'un même texte** (« bis », numéro cadré
      autrement) : la première inscrite reste — l'index inscrit les notations
      exactes avant les notations dérivées, pour qu'un lien existant ne change
      pas d'édition sans raison ;
    * ce sont deux textes **sans parenté** : le mieux attesté l'emporte ; à
      force égale, la clé devient :data:`REFERENCE_AMBIGUE` et aucune citation
      n'y sera rattachée. Au moins l'un des deux a mal lu son numéro, et rien
      ne dit lequel.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        source = args[0] if args else None
        self.strength: dict[tuple[str, str], int] = dict(getattr(source, "strength", {}))

    def claim(self, key: tuple[str, str], document_id: str, strength: int = _EXTERNAL_STRENGTH) -> None:
        current = self.get(key)
        held = self.strength.get(key, _EXTERNAL_STRENGTH)
        if current is None:
            self[key], self.strength[key] = document_id, strength
        elif current == document_id:
            self.strength[key] = max(held, strength)
        elif current != REFERENCE_AMBIGUE and common_root(current) == common_root(document_id):
            return
        elif strength > held:
            self[key], self.strength[key] = document_id, strength
        elif strength == held:
            self[key] = REFERENCE_AMBIGUE

    def add_documents(self, documents: Iterable[Document]) -> None:
        """Inscrit des documents : notations exactes d'abord, dérivées ensuite."""
        derived: list[tuple[tuple[str, str], str, int]] = []
        for document in documents:
            for claim in reference_claims(document):
                if claim.primary:
                    self.claim(claim.key, document.document_id, claim.strength)
                else:
                    derived.append((claim.key, document.document_id, claim.strength))
        for key, document_id, strength in derived:
            self.claim(key, document_id, strength)


def build_reference_index(documents: Iterable[Document]) -> ReferenceIndex:
    """Index ``(type, numéro) -> document_id`` du corpus (voir :class:`ReferenceIndex`)."""
    index = ReferenceIndex()
    index.add_documents(documents)
    return index


# ---------------------------------------------------------------------------
# Détection
# ---------------------------------------------------------------------------


def detect_relations(
    document: Document,
    config: Config,
    profile: JurisdictionProfile | None = None,
) -> list[LegalRelation]:
    """Repère les relations déclarées par un document.

    La recherche porte sur le préambule (« Vu la loi… ») et sur le texte des
    articles, où figurent les clauses d'abrogation finales.
    """
    profile = profile or get_profile(config)
    if not profile or not profile.relation_patterns:
        return []

    relations: list[LegalRelation] = []
    seen: set[tuple[str, str]] = set()
    counter = 0

    # On parcourt par page pour conserver le numéro de page de la citation.
    for page in document.pages:
        for relation_name, patterns in profile.relation_patterns.items():
            try:
                relation_type = RelationType(relation_name)
            except ValueError:
                logger.warning("Type de relation inconnu ignoré : %s", relation_name)
                continue

            for pattern in patterns:
                for match in pattern.finditer(page.text):
                    target = (match.groupdict().get("target") or "").strip(" .,;")
                    if not target:
                        continue
                    key = (relation_name, target.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    counter += 1

                    excerpt = _excerpt(page.text, match.start(), match.end())
                    relations.append(
                        LegalRelation(
                            relation_id=f"{document.document_id}_rel_{counter:03d}",
                            source_document_id=document.document_id,
                            relation=relation_type,
                            target_reference=target,
                            confidence=_base_confidence(relation_type, target),
                            needs_review=True,
                            page=page.page,
                            excerpt=excerpt,
                            article_id=_article_at_page(document, page.page, match.start()),
                        )
                    )

    if relations:
        logger.info("%s : %d relation(s) citée(s)", document.document_id, len(relations))
    return relations


def _base_confidence(relation_type: RelationType, target: str) -> float:
    """Confiance initiale, avant résolution de la cible.

    Une citation comportant un numéro complet est bien plus fiable qu'une
    formule vague (« abroge toutes dispositions antérieures contraires »).
    """
    normalized = normalize_reference(target)
    if normalized is None:
        return 0.35
    # Une simple mention « Vu la loi… » n'emporte aucune conséquence juridique.
    if relation_type is RelationType.CITE:
        return 0.60
    return 0.80


def _excerpt(text: str, start: int, end: int, window: int = 120) -> str:
    """Extrait de contexte autour d'une citation, pour la revue humaine."""
    left = max(0, start - window)
    right = min(len(text), end + window)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _article_at_page(document: Document, page: int, offset: int) -> Optional[str]:
    """Article dans lequel tombe la citation, si identifiable."""
    candidates = [a for a in document.articles if a.page_start <= page <= a.page_end]
    return candidates[-1].article_id if candidates else None


# ---------------------------------------------------------------------------
# Résolution et propagation du statut
# ---------------------------------------------------------------------------


def resolve_relations(
    documents: Sequence[Document],
    config: Config,
    external_index: dict[tuple[str, str], str] | None = None,
) -> RelationReport:
    """Résout les cibles des relations et en déduit les statuts (§13).

    Args:
        documents: documents du lot.
        config: configuration (section ``relations``).
        external_index: index ``(type, numéro) -> document_id`` du corpus déjà
            enregistré, pour relier un nouveau texte à un ancien.

    Returns:
        Le bilan de l'opération. Les statuts sont modifiés **sur les documents
        du lot uniquement** ; les cibles hors lot sont signalées pour être
        mises à jour par le pipeline appelant.
    """
    report = RelationReport()
    min_confidence = float(config.get("relations.min_confidence", 0.80))

    # Le lot s'ajoute à l'index externe sous la même règle : un document du
    # lot qui revendique la clé d'un texte déjà enregistré ne l'écrase pas en
    # silence.
    index = ReferenceIndex(external_index or {})
    index.add_documents(documents)
    by_id = {document.document_id: document for document in documents}

    # Statuts induits, à appliquer après résolution complète.
    induced: dict[str, list[tuple[LegalStatus, float, LegalRelation]]] = defaultdict(list)

    for document in documents:
        for relation in document.relations:
            report.relations_found += 1
            # Une résolution se refait en entier. Sans cela, une seconde passe
            # qui ne trouve plus de cible — numéro devenu ambigu, document
            # désindexé — laisserait en place la cible fausse de la première.
            relation.target_document_id = None
            normalized = normalize_reference(relation.target_reference)

            if normalized is None:
                relation.needs_review = True
                relation.confidence = min(relation.confidence, 0.35)
                report.unresolved += 1
                continue

            target_id = index.get(normalized)
            if target_id is None:
                # Cible absente du corpus : l'information est conservée telle
                # quelle, pour être résolue quand le texte sera importé.
                relation.needs_review = True
                report.unresolved += 1
                continue

            if target_id == REFERENCE_AMBIGUE:
                relation.needs_review = True
                relation.confidence = min(relation.confidence, 0.35)
                report.unresolved += 1
                report.ambiguous += 1
                report.warnings.append(
                    f"{document.document_id} cite {relation.target_reference!r}, "
                    "numéro revendiqué par plusieurs textes sans parenté — non "
                    "rattaché, à vérifier"
                )
                continue

            if target_id == document.document_id:
                # Auto-référence : presque toujours une erreur de lecture.
                relation.needs_review = True
                relation.confidence = 0.20
                report.unresolved += 1
                report.warnings.append(
                    f"{document.document_id} semble se référer à lui-même "
                    f"({relation.target_reference!r}) — à vérifier"
                )
                continue

            relation.target_document_id = target_id
            relation.confidence = round(min(0.99, relation.confidence + 0.15), 4)
            relation.needs_review = relation.confidence < min_confidence
            report.resolved += 1

            status = STATUS_FROM_INCOMING.get(relation.relation)
            if status:
                induced[target_id].append((status, relation.confidence, relation))

    _apply_statuses(induced, by_id, min_confidence, report)

    logger.info(
        "Relations : %d citée(s), %d résolue(s), %d non résolue(s), "
        "%d statut(s) mis à jour, %d signalé(s) pour revue",
        report.relations_found,
        report.resolved,
        report.unresolved,
        report.statuses_updated,
        report.statuses_flagged,
    )
    return report


def _apply_statuses(
    induced: dict[str, list[tuple[LegalStatus, float, LegalRelation]]],
    by_id: dict[str, Document],
    min_confidence: float,
    report: RelationReport,
) -> None:
    """Applique les statuts induits, en n'agissant que sur les cas sûrs."""
    for target_id, candidates in induced.items():
        target = by_id.get(target_id)
        if target is None:
            # La cible n'est pas dans ce lot : on ne touche à rien.
            report.statuses_flagged += 1
            report.warnings.append(
                f"le statut de {target_id} devrait être révisé (relation entrante "
                "détectée hors du lot courant)"
            )
            continue

        confident = [(s, c, r) for s, c, r in candidates if c >= min_confidence]
        if not confident:
            report.statuses_flagged += 1
            target.metadata.warnings.append(
                "relation entrante détectée mais trop incertaine pour modifier le "
                "statut — vérification humaine requise"
            )
            continue

        # Le statut le plus grave l'emporte (abrogé > remplacé > modifié).
        status, confidence, relation = max(
            confident, key=lambda item: (_STATUS_SEVERITY[item[0]], item[1])
        )
        previous = target.metadata.status
        if _STATUS_SEVERITY[status] <= _STATUS_SEVERITY[previous]:
            continue

        target.metadata.status = status
        target.metadata.confidence["status"] = confidence
        target.metadata.evidence["status"] = (
            f"{relation.source_document_id} : {relation.excerpt[:160]}"
        )
        target.metadata.warnings.append(
            f"statut passé de {previous.value} à {status.value} d'après "
            f"{relation.source_document_id} — à confirmer par un juriste"
        )
        report.statuses_updated += 1
        logger.info(
            "%s : statut %s → %s (source : %s, confiance %.2f)",
            target_id,
            previous.value,
            status.value,
            relation.source_document_id,
            confidence,
        )


def annotate_relations(documents: Sequence[Document], config: Config) -> RelationReport:
    """Détecte puis résout les relations d'un lot, en une passe."""
    if not config.get("relations.detect", True):
        return RelationReport(warnings=["détection des relations désactivée"])

    profile = get_profile(config)
    for document in documents:
        document.relations = detect_relations(document, config, profile)
    return resolve_relations(documents, config)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def version_key(document: Document) -> Optional[str]:
    """Clé regroupant les versions successives d'un même texte.

    Deux fichiers portant le même type et le même numéro officiel sont deux
    versions du même texte (consolidation, réédition).
    """
    key = document_reference_key(document)
    return f"{key[0]}_{key[1]}" if key else None


def group_versions(documents: Sequence[Document]) -> dict[str, list[Document]]:
    """Regroupe les documents par texte d'origine."""
    groups: dict[str, list[Document]] = defaultdict(list)
    for document in documents:
        key = version_key(document)
        if key:
            groups[key].append(document)
    return {key: group for key, group in groups.items() if len(group) > 1}


def assign_versions(documents: Sequence[Document]) -> int:
    """Numérote les versions d'un même texte, de la plus ancienne à la plus récente.

    Le tri se fait sur la date du document ; en cas d'égalité ou de date
    manquante, l'identifiant sert de départage stable. Les documents concernés
    reçoivent un avertissement : plusieurs versions d'un même texte appellent
    presque toujours une vérification.
    """
    updated = 0
    for key, group in group_versions(documents).items():
        ordered = sorted(group, key=lambda d: (d.metadata.date or "", d.document_id))
        for index, document in enumerate(ordered, start=1):
            document.metadata.version = str(index)
            document.metadata.warnings.append(
                f"{len(ordered)} versions détectées pour {key} — "
                f"celle-ci est la version {index}"
            )
            updated += 1
    return updated


def relation_graph(documents: Sequence[Document]) -> dict[str, list[dict]]:
    """Graphe orienté des relations, pour visualisation ou export."""
    graph: dict[str, list[dict]] = defaultdict(list)
    for document in documents:
        for relation in document.relations:
            graph[document.document_id].append(
                {
                    "relation": relation.relation.value,
                    "target_document_id": relation.target_document_id,
                    "target_reference": relation.target_reference,
                    "confidence": relation.confidence,
                    "needs_review": relation.needs_review,
                    "page": relation.page,
                }
            )
    return dict(graph)


def unresolved_relations(documents: Sequence[Document]) -> list[LegalRelation]:
    """Relations à trancher par un humain, ordonnées par confiance croissante."""
    pending = [
        relation
        for document in documents
        for relation in document.relations
        if relation.needs_review
    ]
    return sorted(pending, key=lambda relation: relation.confidence)
