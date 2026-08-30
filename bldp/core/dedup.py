"""Module 9 — détection des doublons (§14 du cahier des charges).

Quatre situations sont distinguées, de la plus certaine à la plus incertaine :

``file_hash``
    fichiers binairement identiques — certitude absolue ;
``text_hash``
    textes identiques après normalisation : même document, fichiers différents
    (re-numérisation, conversion, nom différent) ;
``similarity``
    textes très proches sans être identiques — typiquement deux versions d'un
    même texte, ou une réédition ;
``partial``
    un document est largement contenu dans un autre (extrait d'un recueil).

**Aucun document n'est jamais supprimé.** Le pipeline marque les liens de
duplication et laisse la décision à un humain, conformément au §14 : « le
système doit conserver une trace des doublons plutôt que les supprimer
définitivement ».

La similarité repose sur des *shingles* (n-grammes de mots) et l'indice de
Jaccard, calculés sans dépendance externe et en mémoire maîtrisée.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import Document, DuplicateLink
from bldp.utils import hash_text, normalize_for_hash

logger = get_logger("dedup")

#: Nombre maximal de shingles retenus par document, pour borner la mémoire.
#: Sur un code de 400 pages, cela reste largement représentatif.
MAX_SHINGLES = 20000


@dataclass
class DedupReport:
    """Bilan de la détection de doublons sur un lot de documents."""

    documents_compared: int = 0
    identical_files: int = 0
    identical_texts: int = 0
    similar_pairs: int = 0
    partial_pairs: int = 0
    links: list[DuplicateLink] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_links(self) -> int:
        return len(self.links)

    def to_dict(self) -> dict:
        return {
            "documents_compared": self.documents_compared,
            "identical_files": self.identical_files,
            "identical_texts": self.identical_texts,
            "similar_pairs": self.similar_pairs,
            "partial_pairs": self.partial_pairs,
            "total_links": self.total_links,
            "links": [link.to_dict() for link in self.links],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Empreintes
# ---------------------------------------------------------------------------


def document_text_hash(document: Document) -> str:
    """Empreinte du **contenu** d'un document, indépendante de la mise en forme.

    Deux extractions du même texte (natif ou OCR, marges différentes) donnent
    la même empreinte : c'est ce qui permet de reconnaître un doublon dont le
    fichier diffère.
    """
    return hash_text(document.full_text)


def shingles(text: str, size: int = 5, limit: int = MAX_SHINGLES) -> set[str]:
    """Ensemble des n-grammes de mots d'un texte normalisé.

    Args:
        text: texte source.
        size: nombre de mots par shingle.
        limit: borne du nombre de shingles conservés (mémoire).
    """
    words = re.findall(r"\w+", normalize_for_hash(text))
    if len(words) < size:
        return {" ".join(words)} if words else set()

    result: set[str] = set()
    for index in range(len(words) - size + 1):
        result.add(" ".join(words[index : index + size]))
        if len(result) >= limit:
            break
    return result


def jaccard(left: set[str], right: set[str]) -> float:
    """Indice de Jaccard : part d'éléments communs sur l'union."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if not intersection:
        return 0.0
    return intersection / len(left | right)


def containment(small: set[str], large: set[str]) -> float:
    """Part du petit ensemble contenue dans le grand.

    C'est la mesure adaptée au doublon *partiel* : un extrait de 10 pages
    inclus dans un recueil de 400 a une similarité de Jaccard très faible, mais
    un taux d'inclusion proche de 1.
    """
    if not small:
        return 0.0
    return len(small & large) / len(small)


# ---------------------------------------------------------------------------
# Détection
# ---------------------------------------------------------------------------


def find_duplicates(
    documents: Sequence[Document],
    config: Config,
    known_file_hashes: dict[str, str] | None = None,
    known_text_hashes: dict[str, str] | None = None,
) -> DedupReport:
    """Repère les doublons au sein d'un lot et vis-à-vis du corpus déjà en base.

    Args:
        documents: documents traités lors de cette exécution.
        config: configuration (section ``dedup``).
        known_file_hashes: empreintes de fichiers déjà enregistrées
            (``hash -> document_id``).
        known_text_hashes: empreintes de textes déjà enregistrées.

    Returns:
        Le rapport ; les liens sont également attachés à chaque document, de
        sorte qu'ils soient persistés et exportés.
    """
    report = DedupReport(documents_compared=len(documents))
    if not documents:
        return report

    use_file_hash = bool(config.get("dedup.file_hash", True))
    use_text_hash = bool(config.get("dedup.text_hash", True))
    threshold = float(config.get("dedup.similarity_threshold", 0.90))
    shingle_size = int(config.get("dedup.shingle_size", 5))
    action = str(config.get("dedup.action", "flag"))

    if action != "flag":
        report.warnings.append(
            f"dedup.action={action!r} ignoré : les doublons sont toujours marqués, "
            "jamais supprimés (§14)."
        )
        logger.warning("dedup.action=%r ignoré : suppression interdite par le §14.", action)

    file_index: dict[str, str] = dict(known_file_hashes or {})
    text_index: dict[str, str] = dict(known_text_hashes or {})

    # -- 1. fichiers identiques ---------------------------------------------
    if use_file_hash:
        for document in documents:
            digest = document.source.file_hash
            if not digest:
                continue
            original = file_index.get(digest)
            if original and original != document.document_id:
                _link(
                    report, document, original, "file_hash", 1.0,
                    "fichiers binairement identiques (même SHA-256)",
                )
                report.identical_files += 1
            else:
                file_index.setdefault(digest, document.document_id)

    # -- 2. textes identiques ------------------------------------------------
    if use_text_hash:
        for document in documents:
            if not document.text_hash:
                document.text_hash = document_text_hash(document)
            digest = document.text_hash
            if not document.full_text.strip():
                continue
            original = text_index.get(digest)
            if original and original != document.document_id:
                if not _already_linked(document, original):
                    _link(
                        report, document, original, "text_hash", 1.0,
                        "texte identique après normalisation (fichiers différents)",
                    )
                    report.identical_texts += 1
            else:
                text_index.setdefault(digest, document.document_id)

    # -- 3. similarité et inclusion partielle --------------------------------
    if threshold > 0:
        _detect_similar(documents, report, threshold, shingle_size)

    logger.info(
        "Doublons : %d fichier(s) identique(s), %d texte(s) identique(s), "
        "%d paire(s) similaire(s), %d inclusion(s) partielle(s)",
        report.identical_files,
        report.identical_texts,
        report.similar_pairs,
        report.partial_pairs,
    )
    return report


def _detect_similar(
    documents: Sequence[Document],
    report: DedupReport,
    threshold: float,
    shingle_size: int,
) -> None:
    """Compare deux à deux les documents non déjà marqués comme identiques.

    Une pré-sélection par shingles partagés évite la comparaison exhaustive :
    seuls les documents ayant au moins un n-gramme en commun sont confrontés.
    """
    candidates = [d for d in documents if d.full_text.strip()]
    if len(candidates) < 2:
        return

    fingerprints = {
        document.document_id: shingles(document.full_text, shingle_size)
        for document in candidates
    }

    # Index inversé : shingle -> documents. On ne garde qu'un échantillon de
    # shingles par document pour la présélection, afin de rester rapide.
    inverted: dict[str, set[str]] = defaultdict(set)
    for document_id, fingerprint in fingerprints.items():
        for shingle in list(fingerprint)[:2000]:
            inverted[shingle].add(document_id)

    pairs: set[tuple[str, str]] = set()
    for owners in inverted.values():
        if len(owners) < 2:
            continue
        ordered = sorted(owners)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                pairs.add((left, right))

    by_id = {document.document_id: document for document in candidates}

    for left_id, right_id in sorted(pairs):
        left, right = by_id[left_id], by_id[right_id]
        if _already_linked(left, right_id) or _already_linked(right, left_id):
            continue

        left_shingles, right_shingles = fingerprints[left_id], fingerprints[right_id]
        similarity = jaccard(left_shingles, right_shingles)

        if similarity >= threshold:
            _link(
                report, right, left_id, "similarity", round(similarity, 4),
                f"textes très proches (Jaccard {similarity:.2%}) — versions "
                "différentes possibles, vérification humaine requise",
            )
            report.similar_pairs += 1
            continue

        # Inclusion partielle : le plus court est-il contenu dans le plus long ?
        smaller, larger = (
            (left, right) if len(left_shingles) <= len(right_shingles) else (right, left)
        )
        small_set = fingerprints[smaller.document_id]
        large_set = fingerprints[larger.document_id]
        ratio = containment(small_set, large_set)
        if ratio >= threshold and len(small_set) < len(large_set) * 0.9:
            _link(
                report, smaller, larger.document_id, "partial", round(ratio, 4),
                f"{ratio:.0%} du texte est contenu dans {larger.document_id} — "
                "extrait ou recueil probable",
            )
            report.partial_pairs += 1


def _already_linked(document: Document, other_id: str) -> bool:
    return any(link.duplicate_of == other_id for link in document.duplicates)


def _link(
    report: DedupReport,
    document: Document,
    original_id: str,
    kind: str,
    similarity: float,
    details: str,
) -> None:
    """Enregistre un lien de duplication sur le document et dans le rapport."""
    link = DuplicateLink(
        document_id=document.document_id,
        duplicate_of=original_id,
        kind=kind,
        similarity=similarity,
        details=details,
    )
    document.duplicates.append(link)
    report.links.append(link)
    logger.info(
        "Doublon (%s) : %s ≈ %s — %s", kind, document.document_id, original_id, details
    )


# ---------------------------------------------------------------------------
# Doublons internes à un document
# ---------------------------------------------------------------------------


def find_duplicate_pages(document: Document, min_chars: int = 200) -> list[int]:
    """Repère les pages dupliquées à l'intérieur d'un même document.

    Symptôme classique d'une numérisation ayant scanné deux fois la même
    feuille. Les pages courtes sont ignorées : une page de garde presque vide
    ressemble légitimement à une autre.
    """
    seen: dict[str, int] = {}
    duplicates: list[int] = []
    for page in document.pages:
        text = page.text.strip()
        if len(text) < min_chars:
            continue
        digest = hash_text(text)
        if digest in seen:
            duplicates.append(page.page)
        else:
            seen[digest] = page.page
    return duplicates


def load_known_hashes(database) -> tuple[dict[str, str], dict[str, str]]:
    """Charge les empreintes déjà enregistrées, pour comparer aux lots passés."""
    file_hashes: dict[str, str] = {}
    text_hashes: dict[str, str] = {}
    for row in database.connection.execute(
        "SELECT document_id, file_hash, text_hash FROM documents"
    ):
        if row["file_hash"]:
            file_hashes.setdefault(row["file_hash"], row["document_id"])
        if row["text_hash"]:
            text_hashes.setdefault(row["text_hash"], row["document_id"])
    return file_hashes, text_hashes


def iter_duplicate_groups(links: Iterable[DuplicateLink]) -> dict[str, list[DuplicateLink]]:
    """Regroupe les liens par document d'origine, pour une revue manuelle."""
    groups: dict[str, list[DuplicateLink]] = defaultdict(list)
    for link in links:
        groups[link.duplicate_of].append(link)
    return dict(groups)
