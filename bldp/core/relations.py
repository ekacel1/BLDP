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
from typing import Iterable, Optional, Sequence

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
from bldp.utils import slugify

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
    statuses_updated: int = 0
    statuses_flagged: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "relations_found": self.relations_found,
            "resolved": self.resolved,
            "unresolved": self.unresolved,
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


def build_reference_index(documents: Iterable[Document]) -> dict[tuple[str, str], str]:
    """Index ``(type, numéro) -> document_id`` du corpus."""
    index: dict[tuple[str, str], str] = {}
    for document in documents:
        key = document_reference_key(document)
        if key and key not in index:
            index[key] = document.document_id
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

    index = dict(external_index or {})
    index.update(build_reference_index(documents))
    by_id = {document.document_id: document for document in documents}

    # Statuts induits, à appliquer après résolution complète.
    induced: dict[str, list[tuple[LegalStatus, float, LegalRelation]]] = defaultdict(list)

    for document in documents:
        for relation in document.relations:
            report.relations_found += 1
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
