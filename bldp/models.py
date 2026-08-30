"""Modèle de données du pipeline.

Toutes les structures échangées entre modules sont des dataclasses sérialisables
en JSON. La chaîne de traçabilité imposée par le §33 du cahier des charges —
``document original -> page -> texte -> article -> métadonnées -> version`` —
est matérialisée par les champs de provenance présents à chaque niveau
(``source_file``, ``page``, ``document_id``, ``char_start``...).
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Énumérations
# ---------------------------------------------------------------------------


class DocumentType(str, enum.Enum):
    """Nature du texte juridique (§7 du cahier des charges)."""

    LOI = "loi"
    CODE = "code"
    DECRET = "decret"
    ARRETE = "arrete"
    ORDONNANCE = "ordonnance"
    CONSTITUTION = "constitution"
    DECISION = "decision"
    JURISPRUDENCE = "jurisprudence"
    CIRCULAIRE = "circulaire"
    CONVENTION = "convention"
    AUTRE = "autre"
    INCONNU = "inconnu"


class LegalStatus(str, enum.Enum):
    """Statut juridique d'un texte (§13)."""

    EN_VIGUEUR = "en_vigueur"
    MODIFIE = "modifie"
    ABROGE = "abroge"
    PARTIELLEMENT_ABROGE = "partiellement_abroge"
    REMPLACE = "remplace"
    INCONNU = "inconnu"


class RelationType(str, enum.Enum):
    """Nature du lien entre deux textes (§13)."""

    MODIFIE = "modifie"
    ABROGE = "abroge"
    ABROGE_PARTIELLEMENT = "abroge_partiellement"
    REMPLACE = "remplace"
    COMPLETE = "complete"
    APPLIQUE = "applique"
    CITE = "cite"


class ValidationStatus(str, enum.Enum):
    """Décision de validation humaine (§16)."""

    PENDING = "en_attente"
    VALIDATED = "valide"
    TO_REVIEW = "a_verifier"
    REJECTED = "rejete"


class QualityStatus(str, enum.Enum):
    """Verdict automatique du contrôle qualité (§15)."""

    OK = "ok"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class ExtractionMethod(str, enum.Enum):
    """Voie d'extraction réellement empruntée (§7-8)."""

    NATIVE = "pymupdf"
    OCR = "ocr"
    MIXED = "mixed"
    NONE = "none"


class StructureLevel(str, enum.Enum):
    """Niveaux hiérarchiques reconnus par le parser (§10).

    L'ordre de déclaration définit la profondeur : ``PARTIE`` englobe ``LIVRE``,
    qui englobe ``TITRE``, etc. Voir :func:`level_depth`.
    """

    PARTIE = "partie"
    LIVRE = "livre"
    TITRE = "titre"
    SOUS_TITRE = "sous_titre"
    CHAPITRE = "chapitre"
    SECTION = "section"
    SOUS_SECTION = "sous_section"
    PARAGRAPHE = "paragraphe"
    ARTICLE = "article"
    ANNEXE = "annexe"


#: Profondeur hiérarchique de chaque niveau (0 = le plus englobant).
_LEVEL_ORDER: tuple[StructureLevel, ...] = (
    StructureLevel.PARTIE,
    StructureLevel.LIVRE,
    StructureLevel.TITRE,
    StructureLevel.SOUS_TITRE,
    StructureLevel.CHAPITRE,
    StructureLevel.SECTION,
    StructureLevel.SOUS_SECTION,
    StructureLevel.PARAGRAPHE,
    StructureLevel.ARTICLE,
)


def level_depth(level: StructureLevel) -> int:
    """Profondeur d'un niveau ; ``ANNEXE`` est traitée comme une racine."""
    if level is StructureLevel.ANNEXE:
        return 0
    return _LEVEL_ORDER.index(level)


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    """Rend une valeur JSON-compatible, récursivement."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _encode(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_encode(v) for v in value]
    return value


class JsonMixin:
    """Ajoute une sérialisation JSON stable aux dataclasses du modèle."""

    def to_dict(self) -> dict:
        return {k: _encode(v) for k, v in dataclasses.asdict(self).items()}


# ---------------------------------------------------------------------------
# Module 1 — importation
# ---------------------------------------------------------------------------


@dataclass
class SourceFile(JsonMixin):
    """Un fichier brut découvert dans ``input/`` et inventorié.

    Les originaux ne sont jamais modifiés (§18) : ``raw_path`` pointe vers la
    copie de travail, ``source_path`` reste la référence d'origine.
    """

    document_id: str
    source_path: str
    filename: str
    extension: str
    size_bytes: int
    file_hash: str                       # SHA-256 du fichier tel quel
    ingested_at: str
    category: str = "autres"             # sous-dossier d'origine (indicatif)
    raw_path: Optional[str] = None       # copie dans data/raw/
    modified_at: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Module 2 — analyse du PDF
# ---------------------------------------------------------------------------


@dataclass
class PageAnalysis(JsonMixin):
    """Diagnostic d'une page avant extraction."""

    page: int
    char_count: int
    image_count: int
    has_text: bool
    alpha_ratio: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class PdfAnalysis(JsonMixin):
    """Verdict global : le document nécessite-t-il un OCR ? (§7)"""

    document_id: str
    pages: int
    size_bytes: int
    has_text: bool
    ocr_required: bool
    confidence: float
    text_page_ratio: float = 0.0
    total_chars: int = 0
    total_images: int = 0
    mean_chars_per_page: float = 0.0
    encrypted: bool = False
    is_pdf: bool = True
    reasons: list[str] = field(default_factory=list)
    pages_detail: list[PageAnalysis] = field(default_factory=list)
    # Pages textuelles insuffisantes au sein d'un document globalement lisible.
    pages_needing_ocr: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Module 3 — extraction
# ---------------------------------------------------------------------------


@dataclass
class Page(JsonMixin):
    """Texte d'une page, avec sa provenance exacte (§8).

    ``raw_text`` est conservé tel quel pour l'audit ; ``text`` reçoit le texte
    nettoyé une fois le module 4 passé.
    """

    document_id: str
    page: int
    text: str
    source_file: str
    char_count: int = 0
    raw_text: Optional[str] = None
    method: ExtractionMethod = ExtractionMethod.NATIVE
    ocr_confidence: Optional[float] = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.char_count:
            self.char_count = len(self.text)


@dataclass
class ExtractionResult(JsonMixin):
    """Sortie du module d'extraction pour un document."""

    document_id: str
    source_file: str
    method: ExtractionMethod
    pages: list[Page] = field(default_factory=list)
    ocr_pdf_path: Optional[str] = None
    duration_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def total_chars(self) -> int:
        return sum(page.char_count for page in self.pages)


# ---------------------------------------------------------------------------
# Modules 5 & 6 — structure et articles
# ---------------------------------------------------------------------------


@dataclass
class StructureNode(JsonMixin):
    """Un en-tête hiérarchique détecté (Titre, Chapitre, Section...)."""

    node_id: str
    document_id: str
    level: StructureLevel
    number: Optional[str]                # « II », « 3 », « premier »...
    label: str                           # ligne brute telle qu'imprimée
    heading: Optional[str] = None        # intitulé éventuel qui suit le numéro
    page: int = 0
    char_start: int = 0
    char_end: int = 0
    parent_id: Optional[str] = None
    depth: int = 0
    path: list[str] = field(default_factory=list)   # ancêtres, du plus haut au plus bas


@dataclass
class Alinea(JsonMixin):
    """Sous-unité d'un article, conservée dans l'ordre (§11)."""

    index: int
    text: str
    number: Optional[str] = None         # numérotation explicite si présente


@dataclass
class Article(JsonMixin):
    """Unité exploitable centrale du corpus (§11).

    Chaque article conserve son contexte hiérarchique complet et la page où il
    commence, afin de pouvoir remonter à la source d'origine.
    """

    article_id: str
    document_id: str
    article_number: str
    text: str
    label: str = ""                      # en-tête brut (« Article 45 »)
    position: int = 0                    # rang dans le document, à partir de 0
    page_start: int = 0
    page_end: int = 0
    char_start: int = 0
    char_end: int = 0
    partie: Optional[str] = None
    livre: Optional[str] = None
    title: Optional[str] = None          # « Titre II »
    subtitle: Optional[str] = None
    chapter: Optional[str] = None        # « Chapitre III »
    section: Optional[str] = None        # « Section 2 »
    subsection: Optional[str] = None
    annexe: Optional[str] = None
    hierarchy_path: list[str] = field(default_factory=list)
    alineas: list[Alinea] = field(default_factory=list)
    numeric_value: Optional[float] = None   # « 45 bis » -> 45.1, pour le tri
    source_file: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)


# ---------------------------------------------------------------------------
# Module 7 — métadonnées
# ---------------------------------------------------------------------------


@dataclass
class DocumentMetadata(JsonMixin):
    """Métadonnées descriptives d'un document (§12).

    Chaque champ deviné automatiquement est accompagné d'un score dans
    ``confidence`` et de sa justification dans ``evidence`` : le pipeline doit
    pouvoir expliquer d'où vient chaque valeur.
    """

    document_id: str
    title: Optional[str] = None
    type: DocumentType = DocumentType.INCONNU
    number: Optional[str] = None
    date: Optional[str] = None           # ISO-8601 (AAAA-MM-JJ) si connue
    jurisdiction: str = "Benin"
    authority: Optional[str] = None      # autorité émettrice
    legal_domain: Optional[str] = None   # domaine juridique
    language: str = "fr"
    source: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: Optional[str] = None
    version: str = "1"
    status: LegalStatus = LegalStatus.INCONNU
    confidence: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Module 8 — relations entre textes
# ---------------------------------------------------------------------------


@dataclass
class LegalRelation(JsonMixin):
    """Lien orienté entre deux textes (§13).

    ``target_document_id`` reste vide tant que le texte cible n'a pas été
    résolu dans le corpus : la citation est alors conservée telle quelle dans
    ``target_reference`` pour validation humaine.
    """

    relation_id: str
    source_document_id: str
    relation: RelationType
    target_reference: str
    target_document_id: Optional[str] = None
    confidence: float = 0.0
    needs_review: bool = True
    article_id: Optional[str] = None
    page: Optional[int] = None
    excerpt: str = ""


# ---------------------------------------------------------------------------
# Module 9 — doublons
# ---------------------------------------------------------------------------


@dataclass
class DuplicateLink(JsonMixin):
    """Trace d'un doublon : on marque, on ne supprime jamais (§14)."""

    document_id: str
    duplicate_of: str
    kind: str                            # file_hash | text_hash | similarity | partial
    similarity: float = 1.0
    details: str = ""


# ---------------------------------------------------------------------------
# Module 10 — qualité
# ---------------------------------------------------------------------------


@dataclass
class QualityIssue(JsonMixin):
    """Anomalie unitaire détectée sur un document."""

    code: str                            # identifiant stable, ex. "empty_pages"
    severity: str                        # info | warning | error
    message: str
    page: Optional[int] = None
    article_id: Optional[str] = None
    count: int = 1


@dataclass
class QualityReport(JsonMixin):
    """Rapport qualité par document (§15)."""

    document_id: str
    score: float = 0.0
    ocr_quality: Optional[float] = None
    text_quality: float = 0.0
    structure_quality: float = 0.0
    pages: int = 0
    empty_pages: int = 0
    duplicate_pages: int = 0
    missing_pages: int = 0
    articles_detected: int = 0
    numbering_gaps: list[str] = field(default_factory=list)
    possible_errors: int = 0
    status: QualityStatus = QualityStatus.OK
    issues: list[QualityIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Document complet
# ---------------------------------------------------------------------------


@dataclass
class Document(JsonMixin):
    """Agrégat complet d'un document traité — l'unité persistée et exportée."""

    document_id: str
    source: SourceFile
    metadata: DocumentMetadata
    analysis: Optional[PdfAnalysis] = None
    extraction: Optional[ExtractionResult] = None
    structure: list[StructureNode] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    relations: list[LegalRelation] = field(default_factory=list)
    duplicates: list[DuplicateLink] = field(default_factory=list)
    quality: Optional[QualityReport] = None
    validation: ValidationStatus = ValidationStatus.PENDING
    validation_note: str = ""
    text_hash: Optional[str] = None
    processed_at: Optional[str] = None
    pipeline_version: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[Page]:
        return self.extraction.pages if self.extraction else []

    @property
    def full_text(self) -> str:
        return self.extraction.full_text if self.extraction else ""


# ---------------------------------------------------------------------------
# Chunks & embeddings (§19-20)
# ---------------------------------------------------------------------------


@dataclass
class Chunk(JsonMixin):
    """Fragment prêt pour l'indexation, porteur de tout son contexte."""

    chunk_id: str
    document_id: str
    text: str
    article_id: Optional[str] = None
    article_number: Optional[str] = None
    position: int = 0
    page: Optional[int] = None
    title: Optional[str] = None
    chapter: Optional[str] = None
    section: Optional[str] = None
    hierarchy_path: list[str] = field(default_factory=list)
    char_start: int = 0
    char_end: int = 0
    strategy: str = "article"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddingRecord(JsonMixin):
    """Vecteur + métadonnées d'origine (§19)."""

    vector_id: str
    chunk_id: str
    document_id: str
    embedding_model: str
    dimension: int
    article_id: Optional[str] = None
    article_number: Optional[str] = None
    text: str = ""
    vector: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rapport de run
# ---------------------------------------------------------------------------


@dataclass
class RunReport(JsonMixin):
    """Bilan d'une exécution : une erreur ne stoppe jamais le pipeline (§26)."""

    started_at: str
    finished_at: Optional[str] = None
    total: int = 0
    succeeded: int = 0
    review_required: int = 0
    failed: int = 0
    skipped_duplicates: int = 0
    documents: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
