"""Orchestration du pipeline complet.

Enchaîne les modules dans l'ordre imposé par l'architecture (§5) :

.. code-block:: text

    Document Loader → PDF Classifier → PyMuPDF / OCR → Text Normalizer
    → Legal Parser → Metadata Engine → Relations → Dedup → Quality Checker
    → SQLite / JSONL → (Chunking → Embeddings → FAISS)

Deux exigences structurent le module :

* **§26 — une erreur sur un document n'arrête pas le pipeline.** Chaque
  document est traité dans son propre bloc protégé ; un échec est consigné,
  compté, et le traitement continue. Le rapport final distingue les documents
  réussis, ceux à vérifier et ceux en échec.
* **§33 — la chaîne de traçabilité est conservée d'un bout à l'autre.** Le
  :class:`~bldp.models.Document` produit porte à la fois le fichier source, les
  pages (texte brut *et* nettoyé), la structure, les articles, les métadonnées
  avec leurs preuves, les relations et le rapport qualité.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from bldp import __version__
from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import (
    Chunk,
    Document,
    DocumentMetadata,
    ExtractionMethod,
    QualityStatus,
    RunReport,
    SourceFile,
    ValidationStatus,
)
from bldp.core.classifier import analyze_or_none, decide_extraction_route
from bldp.core.cleaning.normalizer import clean_pages
from bldp.core.dedup import document_text_hash, find_duplicates, load_known_hashes
from bldp.core.extraction.ocr import extract_with_route
from bldp.core.extraction.pymupdf_extractor import ExtractionError, extract_pdf_metadata
from bldp.core.loader import ingest
from bldp.core.metadata.engine import extract_metadata
from bldp.core.parser.legal_parser import parse_document
from bldp.core.relations import annotate_relations, assign_versions
from bldp.core.storage.exporters import export_all, export_run_report
from bldp.core.validation.quality import evaluate_all, suggest_validation, summarize
from bldp.jurisdictions.registry import get_profile, get_ruleset
from bldp.utils import timestamp_slug, utc_now_iso, write_json

logger = get_logger("pipeline")

#: Signature d'un rappel de progression : ``(rang, total, document_id, étape)``.
ProgressCallback = Callable[[int, int, str, str], None]


@dataclass
class PipelineResult:
    """Sortie complète d'une exécution."""

    documents: list[Document] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    report: Optional[RunReport] = None
    exports: dict[str, str] = field(default_factory=dict)
    index_path: Optional[str] = None
    embeddings_count: int = 0

    @property
    def succeeded(self) -> list[Document]:
        return [d for d in self.documents if not d.errors]

    @property
    def review_required(self) -> list[Document]:
        """Documents exploitables mais douteux.

        Les documents en **échec** en sont exclus : ils relèvent d'une autre
        catégorie, et les compter deux fois fausserait le bilan du §26.
        """
        return [
            d
            for d in self.documents
            if not d.errors and d.quality and d.quality.status is not QualityStatus.OK
        ]

    @property
    def failed(self) -> list[Document]:
        return [d for d in self.documents if d.errors]


# ---------------------------------------------------------------------------
# Traitement d'un document
# ---------------------------------------------------------------------------


def process_source(
    source: SourceFile,
    config: Config,
    ruleset: Any = None,
    profile: Any = None,
) -> Document:
    """Traite un document, de l'analyse PDF au parsing.

    Le document renvoyé est toujours exploitable, même partiellement : en cas
    d'échec, il porte l'erreur dans ``errors`` plutôt que de disparaître du
    corpus (§26). Les étapes suivantes (relations, doublons, qualité) sont
    appliquées à l'échelle du lot par :func:`run_pipeline`.
    """
    document = Document(
        document_id=source.document_id,
        source=source,
        metadata=DocumentMetadata(document_id=source.document_id),
        pipeline_version=__version__,
        processed_at=utc_now_iso(),
    )

    path = Path(source.raw_path or source.source_path)
    if source.error:
        document.errors.append(source.error)
    if not path.exists():
        document.errors.append(f"fichier introuvable : {path}")
        return document

    ruleset = ruleset if ruleset is not None else get_ruleset(config)

    # -- Module 2 : analyse et décision d'OCR -------------------------------
    analysis = analyze_or_none(path, source.document_id, config)
    if analysis is None:
        document.errors.append("analyse impossible : PDF illisible ou corrompu")
        return document
    document.analysis = analysis
    route = decide_extraction_route(analysis, config)

    # -- Modules 3 et 4 : extraction ----------------------------------------
    try:
        extraction = extract_with_route(
            path,
            source.document_id,
            route,
            config,
            ocr_pages=analysis.pages_needing_ocr,
            source_file=source.filename,
        )
    except ExtractionError as exc:
        document.errors.append(f"extraction impossible : {exc}")
        logger.error("%s : extraction impossible (%s)", source.document_id, exc)
        return document

    document.extraction = extraction
    document.errors.extend(extraction.errors)

    # -- Module 5 : nettoyage ------------------------------------------------
    cleaned, cleaning_report = clean_pages(extraction.pages, config, source.document_id)
    extraction.pages = cleaned
    for warning in cleaning_report.warnings:
        document.metadata.warnings.append(f"nettoyage : {warning}")

    document.text_hash = document_text_hash(document)

    # -- Modules 5 et 6 : parsing -------------------------------------------
    parse_result = parse_document(
        cleaned, source.document_id, config, ruleset, source.filename
    )
    document.structure = parse_result.structure
    document.articles = parse_result.articles
    for warning in parse_result.warnings:
        document.metadata.warnings.append(f"parsing : {warning}")

    # -- Module 7 : métadonnées ---------------------------------------------
    pdf_metadata: dict = {}
    try:
        pdf_metadata = extract_pdf_metadata(path)
    except ExtractionError:
        pass  # indices facultatifs : leur absence n'est pas une erreur

    warnings_so_far = list(document.metadata.warnings)
    document.metadata = extract_metadata(
        source.document_id, cleaned, config, source, pdf_metadata, profile
    )
    document.metadata.warnings = warnings_so_far + document.metadata.warnings

    return document


# ---------------------------------------------------------------------------
# Pipeline complet
# ---------------------------------------------------------------------------


def run_pipeline(
    input_path: str | Path,
    config: Config,
    limit: int | None = None,
    do_export: bool = True,
    do_embeddings: bool | None = None,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
    """Exécute le pipeline complet sur un dossier d'entrée.

    Args:
        input_path: dossier ou fichier à traiter.
        config: configuration effective.
        limit: ne traiter que les ``limit`` premiers documents (mise au point).
        do_export: produire les exports et la base SQLite.
        do_embeddings: forcer l'activation/désactivation des embeddings ;
            ``None`` = suivre la configuration.
        progress: rappel de progression, pour l'interface web ou la CLI.

    Returns:
        Le résultat complet : documents, fragments, rapport et exports.
    """
    started = time.perf_counter()
    config.ensure_directories()
    result = PipelineResult()
    report = RunReport(started_at=utc_now_iso())

    ruleset = get_ruleset(config)
    profile = get_profile(config)

    # -- Module 1 : importation ---------------------------------------------
    sources = ingest(input_path, config)
    if limit:
        sources = sources[:limit]
    report.total = len(sources)

    if not sources:
        logger.warning("Aucun document à traiter dans %s", input_path)
        report.finished_at = utc_now_iso()
        result.report = report
        return result

    # -- Traitement document par document -----------------------------------
    for rank, source in enumerate(sources, start=1):
        if progress:
            progress(rank, len(sources), source.document_id, "traitement")
        logger.info("[%d/%d] %s", rank, len(sources), source.document_id)
        try:
            document = process_source(source, config, ruleset, profile)
        except Exception as exc:  # noqa: BLE001 — §26 : jamais d'arrêt du lot
            logger.error(
                "Erreur inattendue sur %s : %s", source.document_id, exc, exc_info=True
            )
            document = Document(
                document_id=source.document_id,
                source=source,
                metadata=DocumentMetadata(document_id=source.document_id),
                pipeline_version=__version__,
                processed_at=utc_now_iso(),
                errors=[f"erreur inattendue : {exc}"],
            )
            report.errors.append(
                {
                    "document_id": source.document_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=5),
                }
            )
        result.documents.append(document)

    documents = result.documents

    # -- Module 9 : doublons -------------------------------------------------
    known_files: dict[str, str] = {}
    known_texts: dict[str, str] = {}
    if do_export:
        known_files, known_texts = _load_known_hashes(config)
    dedup_report = find_duplicates(documents, config, known_files, known_texts)

    # -- Module 8 : relations et versions ------------------------------------
    if progress:
        progress(len(sources), len(sources), "—", "relations")
    relation_report = annotate_relations(documents, config)
    assign_versions(documents)

    # -- Module 10 : qualité --------------------------------------------------
    if progress:
        progress(len(sources), len(sources), "—", "qualité")
    evaluate_all(documents, config)
    for document in documents:
        if document.quality and document.validation is ValidationStatus.PENDING:
            document.validation = suggest_validation(document.quality)

    # -- Comptage (§26) -------------------------------------------------------
    for document in documents:
        entry = {
            "document_id": document.document_id,
            "filename": document.source.filename,
            "pages": len(document.pages),
            "articles": len(document.articles),
            "quality_score": document.quality.score if document.quality else None,
            "quality_status": document.quality.status.value if document.quality else None,
            "validation": document.validation.value,
            "errors": document.errors,
        }
        report.documents.append(entry)

        if document.errors:
            report.failed += 1
            report.errors.append(
                {"document_id": document.document_id, "error": "; ".join(document.errors)}
            )
        elif document.quality and document.quality.status is not QualityStatus.OK:
            report.review_required += 1
        else:
            report.succeeded += 1

    report.skipped_duplicates = dedup_report.identical_files + dedup_report.identical_texts

    # -- Chunking, embeddings, index -----------------------------------------
    wants_embeddings = (
        bool(config.get("embeddings.enabled", False)) if do_embeddings is None else do_embeddings
    )
    records: list = []
    if wants_embeddings:
        if progress:
            progress(len(sources), len(sources), "—", "embeddings")
        result.chunks, records, result.index_path = _run_embeddings(documents, config)
        result.embeddings_count = len(records)
    else:
        from bldp.core.chunking import chunk_documents

        # Les fragments sont produits même sans embeddings : ils sont utiles
        # tels quels, et permettent d'indexer plus tard sans tout retraiter.
        result.chunks = chunk_documents(documents, config)

    # -- Exports --------------------------------------------------------------
    if do_export:
        if progress:
            progress(len(sources), len(sources), "—", "export")
        result.exports = export_all(documents, config, result.chunks)
        # Les vecteurs sont persistés **après** l'export : les tables `chunks`
        # et `embeddings` référencent `documents`, qui n'existe en base qu'une
        # fois l'export effectué. Les écrire plus tôt violerait la contrainte
        # de clé étrangère, et les vecteurs seraient silencieusement perdus.
        if records:
            _persist_embeddings(records, config)
    elif records:
        logger.warning(
            "Export désactivé : %d vecteur(s) générés mais non persistés en base.",
            len(records),
        )

    report.finished_at = utc_now_iso()
    result.report = report

    run_log = config.path("exports") / f"run_report_{timestamp_slug()}.json"
    export_run_report(report, run_log)
    result.exports["run_report"] = str(run_log)

    write_json(
        config.path("exports") / "pipeline_summary.json",
        {
            "generated_at": utc_now_iso(),
            "pipeline_version": __version__,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "documents": {
                "total": report.total,
                "succeeded": report.succeeded,
                "review_required": report.review_required,
                "failed": report.failed,
            },
            "quality": summarize([d.quality for d in documents if d.quality]),
            "duplicates": dedup_report.to_dict(),
            "relations": relation_report.to_dict(),
            "chunks": len(result.chunks),
            "embeddings": result.embeddings_count,
            "vector_index": result.index_path,
        },
    )

    logger.info(
        "Pipeline terminé en %.1fs : %d document(s) — %d réussi(s), "
        "%d à vérifier, %d en échec",
        time.perf_counter() - started,
        report.total,
        report.succeeded,
        report.review_required,
        report.failed,
    )
    return result


def _load_known_hashes(config: Config) -> tuple[dict[str, str], dict[str, str]]:
    """Empreintes déjà enregistrées, pour reconnaître un doublon d'un lot passé."""
    database_path = config.path("database")
    if not database_path.exists():
        return {}, {}
    try:
        from bldp.core.storage.sqlite_store import LegalDatabase

        with LegalDatabase(database_path, create=False) as database:
            return load_known_hashes(database)
    except Exception as exc:  # base absente, verrouillée ou d'une version ancienne
        logger.warning("Empreintes existantes illisibles (%s) : dédoublonnage limité au lot.", exc)
        return {}, {}


def _persist_embeddings(records: Sequence[Any], config: Config) -> None:
    """Enregistre les vecteurs en base, une fois les documents présents."""
    try:
        from bldp.core.storage.sqlite_store import LegalDatabase

        with LegalDatabase(config.path("database")) as database:
            database.save_embeddings(records)
    except Exception as exc:  # base verrouillée, schéma ancien…
        logger.warning("Vecteurs non persistés en base : %s", exc)


def _run_embeddings(
    documents: Sequence[Document], config: Config
) -> tuple[list[Chunk], list, Optional[str]]:
    """Chunking, embeddings et indexation — chaque étape dégrade proprement.

    Ne touche pas à la base : la persistance est faite par l'appelant, après
    l'export des documents (contrainte de clé étrangère).
    """
    from bldp.core.chunking import chunk_documents
    from bldp.core.embeddings import (
        EmbeddingError,
        EmbeddingsUnavailableError,
        check_embeddings_ready,
        embed_chunks,
    )
    from bldp.core.vectorstore import index_embeddings

    chunks = chunk_documents(documents, config)
    if not chunks:
        return [], [], None

    ready, problems = check_embeddings_ready(config)
    if not ready:
        logger.warning("Embeddings ignorés : %s", " ; ".join(problems))
        return chunks, [], None

    try:
        records = embed_chunks(chunks, config)
    except (EmbeddingsUnavailableError, EmbeddingError) as exc:
        logger.error("Embeddings impossibles : %s — le corpus reste complet.", exc)
        return chunks, [], None

    index_path = index_embeddings(records, config)
    return chunks, records, str(index_path) if index_path else None


# ---------------------------------------------------------------------------
# Étapes isolées (commandes `process`, `validate`, `embed`, `export`)
# ---------------------------------------------------------------------------


def process_only(
    input_path: str | Path,
    config: Config,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
    """Traite les documents et écrit ``data/processed/`` sans exporter le corpus."""
    result = run_pipeline(
        input_path, config, limit=limit, do_export=False, do_embeddings=False, progress=progress
    )
    destination = config.path("processed")
    destination.mkdir(parents=True, exist_ok=True)
    for document in result.documents:
        write_json(destination / f"{document.document_id}.json", document.to_dict())
    logger.info("%d document(s) écrit(s) dans %s", len(result.documents), destination)
    return result


def load_processed(directory: str | Path) -> list[dict]:
    """Relit les documents traités depuis ``data/processed/``."""
    from bldp.utils import read_json

    folder = Path(directory)
    if not folder.exists():
        return []
    return [read_json(path) for path in sorted(folder.glob("*.json"))]
