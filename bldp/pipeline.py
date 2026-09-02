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
    #: Documents déjà traités lors d'une exécution antérieure et non retraités.
    skipped_existing: list[str] = field(default_factory=list)
    #: Tickets de suivi ouverts ou mis à jour par l'exécution.
    tickets: list = field(default_factory=list)
    #: PDF OCRisés supprimés par la politique de rétention, et octets libérés.
    purged_ocr_pdfs: int = 0
    purged_bytes: int = 0

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
    catalogue: Any = None,
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

    # -- Module 7 bis : confrontation au catalogue de collecte --------------
    #
    # Le collecteur en sait plus que le document sur certains points : l'URL
    # de la page d'origine ne figure nulle part dans le PDF, et un numéro
    # illisible sur un scan est lisible sur la fiche. Le document garde le
    # dernier mot partout ailleurs ; ce qui diverge est signalé, pas remplacé.
    _confronter_au_catalogue(document, source, catalogue)

    return document


def _load_catalogue_or_warn(config: Config) -> Any:
    """Charge le catalogue si la configuration en déclare un.

    Un catalogue illisible n'arrête pas le lot (§26) — mais il est dit
    bruyamment, parce que traiter tout un corpus en croyant disposer d'une
    vérification qu'on n'a pas est pire que de ne pas l'avoir demandée.
    """
    from bldp.core.crawl import CrawlIndexError, load_catalogue

    try:
        return load_catalogue(config)
    except CrawlIndexError as exc:
        logger.error(
            "Catalogue de collecte inutilisable : %s — le lot est traité SANS "
            "confrontation au catalogue.", exc,
        )
        return None


def _confronter_au_catalogue(
    document: Document, source: SourceFile, catalogue: Any
) -> None:
    """Met la fiche du collecteur en regard des métadonnées lues.

    La jointure se fait sur l'**empreinte du contenu**, pas sur le nom du
    fichier : un document renommé ou recopié retrouve sa fiche. Un document
    absent du catalogue n'est pas une anomalie — le corpus peut contenir des
    pièces qui ne viennent pas d'une collecte.
    """
    if not catalogue:
        return

    from bldp.core.crawl import normalize_hash, reconcile

    fiche = catalogue.get(normalize_hash(source.file_hash))
    if fiche is None:
        return

    ecarts = reconcile(document.metadata, fiche)
    divergences = [e for e in ecarts if e.action == "diverge"]
    if divergences:
        logger.info(
            "%s : %d divergence(s) avec le catalogue (%s).",
            document.document_id, len(divergences),
            ", ".join(sorted({e.field for e in divergences})),
        )


# ---------------------------------------------------------------------------
# Reprise incrémentale
# ---------------------------------------------------------------------------


def load_completed_hashes(config: Config) -> dict[str, str]:
    """Empreintes des documents déjà traités **avec succès**.

    Un document ayant échoué lors d'une exécution antérieure n'y figure pas :
    il doit être retenté, sans quoi une panne transitoire (verrou de fichier,
    OCR interrompu) se transformerait en perte définitive.
    """
    database_path = config.path("database")
    if not database_path.exists():
        return {}

    try:
        from bldp.core.storage.sqlite_store import LegalDatabase

        with LegalDatabase(database_path, create=False) as database:
            return {
                row["file_hash"]: row["document_id"]
                for row in database.connection.execute(
                    "SELECT document_id, file_hash, errors_json FROM documents "
                    "WHERE file_hash IS NOT NULL AND file_hash != ''"
                )
                if row["errors_json"] in (None, "", "[]")
            }
    except Exception as exc:  # base absente, verrouillée, schéma ancien
        logger.warning(
            "Reprise impossible (%s) : tous les documents seront retraités.", exc
        )
        return {}


def _partition_sources(
    sources: Sequence[SourceFile], completed: dict[str, str]
) -> tuple[list[SourceFile], list[str]]:
    """Sépare ce qui reste à traiter de ce qui l'a déjà été."""
    to_process: list[SourceFile] = []
    skipped: list[str] = []
    for source in sources:
        known = completed.get(source.file_hash) if source.file_hash else None
        if known:
            skipped.append(known)
        else:
            to_process.append(source)
    return to_process, skipped


# ---------------------------------------------------------------------------
# Rétention des PDF OCRisés
# ---------------------------------------------------------------------------


def purge_ocr_pdfs(documents: Sequence[Document], policy: str) -> tuple[int, int]:
    """Applique la politique de rétention des PDF OCRisés.

    Args:
        policy: ``"all"`` (tout conserver), ``"review"`` (ne garder que les
            documents à vérifier) ou ``"none"``.

    Le PDF OCRisé est ce qui permet de comparer le texte extrait à l'image
    d'origine. Le supprimer, c'est renoncer à cette vérification — d'où le mode
    ``review``, qui le conserve précisément là où un humain devra trancher.

    Returns:
        ``(fichiers_supprimés, octets_libérés)``.
    """
    if policy not in {"all", "review", "none"}:
        logger.warning("Politique de rétention inconnue (%s) : tout est conservé.", policy)
        return 0, 0
    if policy == "all":
        return 0, 0

    removed = freed = 0
    for document in documents:
        extraction = document.extraction
        if not extraction or not extraction.ocr_pdf_path:
            continue
        if policy == "review":
            suspect = bool(document.errors) or (
                document.quality is not None
                and document.quality.status is not QualityStatus.OK
            )
            if suspect:
                continue

        path = Path(extraction.ocr_pdf_path)
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
        extraction.ocr_pdf_path = None
        extraction.warnings.append(
            "PDF OCRisé supprimé par la politique de rétention "
            f"(ocr.keep_sidecar_for={policy})"
        )

    if removed:
        logger.info(
            "Rétention « %s » : %d PDF OCRisé(s) supprimé(s), %.0f Mo libérés.",
            policy,
            removed,
            freed / 1e6,
        )
    return removed, freed


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
    resume: bool | None = None,
    workers: int | None = None,
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
        resume: sauter les documents déjà traités avec succès ; ``None`` =
            suivre ``pipeline.resume``.
        workers: nombre de documents traités de front ; ``None`` = suivre
            ``pipeline.workers``, ``0`` = un par cœur.

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

    # -- Reprise : écarter ce qui a déjà été traité avec succès -------------
    should_resume = (
        bool(config.get("pipeline.resume", False)) if resume is None else resume
    )
    if should_resume:
        completed = load_completed_hashes(config)
        # Un document dont un humain a déjà réglé le sort est écarté lui aussi :
        # le rejouer remplacerait sa décision par un verdict automatique.
        completed.update(_settled_by_tracking(config))
        sources, result.skipped_existing = _partition_sources(sources, completed)
        if result.skipped_existing:
            logger.info(
                "Reprise : %d document(s) déjà traité(s) ignoré(s), %d à traiter.",
                len(result.skipped_existing),
                len(sources),
            )

    report.total = len(sources)

    if not sources:
        if result.skipped_existing:
            logger.info("Rien de nouveau à traiter : le corpus est à jour.")
        else:
            logger.warning("Aucun document à traiter dans %s", input_path)
        # Le bilan doit rester complet même sur cette sortie anticipée, sinon
        # une reprise intégrale semblerait n'avoir rien fait du tout.
        report.skipped_existing = list(result.skipped_existing)
        report.finished_at = utc_now_iso()
        result.report = report
        if do_export and result.skipped_existing:
            # Les exports restent régénérés : ils doivent refléter le corpus
            # complet, même quand rien n'a changé.
            result.exports = _export_corpus(config, result.chunks)
        return result

    # -- Traitement, éventuellement parallèle --------------------------------
    worker_count = _resolve_workers(config, workers, len(sources))
    if worker_count > 1:
        # Chaque ocrmypdf parallélise déjà en interne : sans ce garde-fou, N
        # processus × M fils saturent la machine et ralentissent l'ensemble.
        if not config.get("ocr.jobs"):
            config = config.with_overrides({"ocr": {"jobs": 1}})
        logger.info("Traitement de %d document(s) sur %d fil(s).", len(sources), worker_count)

    # Le catalogue de collecte est chargé une fois pour tout le lot : il tient
    # en mémoire, et un dictionnaire se partage entre fils là où une connexion
    # SQLite ne le ferait pas.
    catalogue = _load_catalogue_or_warn(config)

    result.documents = _process_sources(
        sources, config, ruleset, profile, report, worker_count, progress, catalogue
    )
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
    report.skipped_existing = list(result.skipped_existing)

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

    # -- Journal de suivi ----------------------------------------------------
    # Consigné **après** le contrôle qualité : l'étape proposée à chaque
    # ticket dépend de ce que la qualité a constaté.
    if config.get("tracking.enabled", True):
        result.tickets = _record_tracking(documents, config)

    # -- Rétention des PDF OCRisés -------------------------------------------
    # Appliquée **après** le contrôle qualité : le mode « review » conserve le
    # PDF précisément pour les documents qu'un humain devra examiner.
    policy = str(config.get("ocr.keep_sidecar_for", "all"))
    result.purged_ocr_pdfs, result.purged_bytes = purge_ocr_pdfs(documents, policy)

    # -- Exports --------------------------------------------------------------
    if do_export:
        if progress:
            progress(len(sources), len(sources), "—", "export")
        result.exports = export_all(documents, config, result.chunks)
        if result.skipped_existing:
            # Des documents ont été sautés : les fichiers doivent refléter le
            # corpus entier, pas seulement le lot courant.
            result.exports.update(_export_corpus(config, result.chunks))
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


def _record_tracking(documents: Sequence[Document], config: Config) -> list:
    """Consigne le lot dans le registre de suivi.

    Le suivi ne doit jamais faire échouer un traitement : s'il est
    indisponible, on le signale et le corpus reste produit (§26).
    """
    try:
        from bldp.core.tracking import TrackingRegistry

        with TrackingRegistry(config.path("database")) as registry:
            tickets = registry.record_batch(documents)
        logger.info("Suivi : %d ticket(s) mis à jour.", len(tickets))
        return tickets
    except Exception as exc:  # noqa: BLE001
        logger.warning("Registre de suivi indisponible : %s", exc)
        return []


def _settled_by_tracking(config: Config) -> dict[str, str]:
    """Empreintes dont un humain a déjà réglé le sort.

    Retraiter un document validé ou rejeté écraserait un travail humain par
    un résultat automatique. La reprise les écarte donc au même titre que les
    documents déjà traités avec succès.
    """
    if not config.get("tracking.enabled", True):
        return {}
    try:
        from bldp.core.tracking import TrackingRegistry

        database = config.path("database")
        if not database.exists():
            return {}
        with TrackingRegistry(database, create=False) as registry:
            return registry.settled_hashes()
    except Exception:  # noqa: BLE001 — le suivi ne bloque jamais le pipeline
        return {}


def _resolve_workers(config: Config, workers: int | None, count: int) -> int:
    """Nombre de documents traités de front.

    ``0`` signifie « un par cœur », plafonné au nombre de documents : lancer
    plus de fils que de documents ne sert à rien.
    """
    import os

    requested = int(config.get("pipeline.workers", 1)) if workers is None else int(workers)
    if requested <= 0:
        requested = os.cpu_count() or 1
    return max(1, min(requested, count))


def _failed_document(source: SourceFile, exc: Exception) -> Document:
    """Document minimal conservant l'erreur — il reste dans le corpus (§26)."""
    return Document(
        document_id=source.document_id,
        source=source,
        metadata=DocumentMetadata(document_id=source.document_id),
        pipeline_version=__version__,
        processed_at=utc_now_iso(),
        errors=[f"erreur inattendue : {exc}"],
    )


def _process_sources(
    sources: Sequence[SourceFile],
    config: Config,
    ruleset: Any,
    profile: Any,
    report: RunReport,
    workers: int,
    progress: ProgressCallback | None,
    catalogue: Any = None,
) -> list[Document]:
    """Traite les documents, séquentiellement ou en parallèle.

    Le parallélisme repose sur des **fils** et non des processus : l'essentiel
    du temps est passé dans OCRmyPDF et Tesseract, des sous-processus qui
    n'occupent pas l'interpréteur. Cela évite au passage de sérialiser les
    documents entre processus.

    L'ordre du résultat suit toujours celui des sources, quel que soit l'ordre
    d'achèvement : deux exécutions du même lot produisent le même corpus.
    """
    import threading

    total = len(sources)
    results: list[Optional[Document]] = [None] * total
    lock = threading.Lock()
    done = 0

    def handle(index: int, source: SourceFile) -> None:
        nonlocal done
        try:
            document = process_source(source, config, ruleset, profile, catalogue)
        except Exception as exc:  # noqa: BLE001 — §26 : jamais d'arrêt du lot
            logger.error(
                "Erreur inattendue sur %s : %s", source.document_id, exc, exc_info=True
            )
            document = _failed_document(source, exc)
            with lock:
                report.errors.append(
                    {
                        "document_id": source.document_id,
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=5),
                    }
                )
        with lock:
            results[index] = document
            done += 1
            logger.info("[%d/%d] %s", done, total, source.document_id)
            if progress:
                progress(done, total, source.document_id, "traitement")

    if workers <= 1:
        for index, source in enumerate(sources):
            handle(index, source)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda item: handle(*item), enumerate(sources)))

    return [document for document in results if document is not None]


def _export_corpus(config: Config, chunks: Sequence[Chunk]) -> dict[str, str]:
    """Régénère les exports à partir de **tout** le corpus enregistré.

    Indispensable dès qu'une exécution saute des documents déjà traités :
    exporter le seul lot courant tronquerait ``documents.jsonl`` et
    ``articles.jsonl`` au dernier lot, effaçant silencieusement le reste du
    corpus.
    """
    from bldp.core.storage.sqlite_store import LegalDatabase, load_documents

    database_path = config.path("database")
    if not database_path.exists():
        return {}

    with LegalDatabase(database_path, create=False) as database:
        documents = load_documents(database)

    # La base est déjà à jour : on ne réécrit que les fichiers.
    file_formats = [
        fmt for fmt in config.get("export.formats", []) if str(fmt).lower() != "sqlite"
    ]
    produced = export_all(
        documents, config.with_overrides({"export": {"formats": file_formats}}), chunks
    )
    produced["sqlite"] = str(database_path)
    logger.info("Exports régénérés à partir du corpus complet (%d documents).", len(documents))
    return produced


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
    resume: bool | None = None,
    workers: int | None = None,
) -> PipelineResult:
    """Traite les documents et écrit ``data/processed/`` sans exporter le corpus."""
    result = run_pipeline(
        input_path,
        config,
        limit=limit,
        do_export=False,
        do_embeddings=False,
        progress=progress,
        resume=resume,
        workers=workers,
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
