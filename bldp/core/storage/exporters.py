"""Exports du corpus (§17 du cahier des charges).

Formats produits par le MVP :

``documents.jsonl`` / ``articles.jsonl``
    une ligne JSON par document / par article, format d'échange privilégié
    pour l'indexation et le RAG ;
``metadata.json``
    métadonnées consolidées du corpus, avec confiances et preuves ;
``quality_report.json``
    rapport qualité agrégé (§15) ;
``chunks.jsonl``
    fragments prêts pour l'embedding, avec tout leur contexte (§20).

CSV et Parquet sont prévus « ultérieurement » par le cahier des charges : le CSV
est fourni car il ne coûte qu'une dépendance déjà présente (module standard),
Parquet reste hors périmètre du MVP.

Chaque enregistrement exporté conserve sa provenance (``source_file``,
``page_start``…) : un article doit toujours pouvoir être retrouvé dans son PDF
d'origine (§33).
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import Chunk, Document, QualityStatus
from bldp.utils import utc_now_iso, write_json, write_jsonl

logger = get_logger("storage.export")


# ---------------------------------------------------------------------------
# Mise en forme des enregistrements
# ---------------------------------------------------------------------------


def document_record(document: Document, include_pages: bool = True) -> dict:
    """Enregistrement JSONL d'un document."""
    metadata = document.metadata
    record: dict[str, Any] = {
        "document_id": document.document_id,
        "title": metadata.title,
        "type": metadata.type.value,
        "number": metadata.number,
        "date": metadata.date,
        "jurisdiction": metadata.jurisdiction,
        "authority": metadata.authority,
        "legal_domain": metadata.legal_domain,
        "language": metadata.language,
        "source": metadata.source,
        "source_url": metadata.source_url,
        "retrieved_at": metadata.retrieved_at,
        "version": metadata.version,
        "status": metadata.status.value,
        # Provenance : indispensable pour auditer une information.
        "source_file": document.source.filename,
        "source_path": document.source.source_path,
        "file_hash": document.source.file_hash,
        "text_hash": document.text_hash,
        "category": document.source.category,
        "page_count": len(document.pages),
        "article_count": len(document.articles),
        "extraction_method": document.extraction.method.value if document.extraction else None,
        "ocr_required": document.analysis.ocr_required if document.analysis else None,
        "validation": document.validation.value,
        "validation_note": document.validation_note,
        "processed_at": document.processed_at,
        "pipeline_version": document.pipeline_version,
        "quality_score": document.quality.score if document.quality else None,
        "quality_status": document.quality.status.value if document.quality else None,
        "metadata_confidence": metadata.confidence,
        "metadata_evidence": metadata.evidence,
        "warnings": list(metadata.warnings),
        "errors": list(document.errors),
        "duplicates": [link.to_dict() for link in document.duplicates],
        "relations": [relation.to_dict() for relation in document.relations],
        "structure": [node.to_dict() for node in document.structure],
    }
    if include_pages:
        record["pages"] = [
            {
                "page": page.page,
                "text": page.text,
                "char_count": page.char_count,
                "method": page.method.value,
                "ocr_confidence": page.ocr_confidence,
                "warnings": page.warnings,
                "source_file": page.source_file,
            }
            for page in document.pages
        ]
    return record


def article_record(document: Document, article) -> dict:
    """Enregistrement JSONL d'un article, enrichi du contexte documentaire.

    L'article porte les métadonnées de son document : un consommateur du corpus
    (moteur RAG) n'a alors pas besoin de faire de jointure pour citer sa source.
    """
    metadata = document.metadata
    return {
        "article_id": article.article_id,
        "document_id": article.document_id,
        "article_number": article.article_number,
        "numeric_value": article.numeric_value,
        "text": article.text,
        "label": article.label,
        "position": article.position,
        "alineas": [alinea.to_dict() for alinea in article.alineas],
        # Contexte hiérarchique (§11)
        "partie": article.partie,
        "livre": article.livre,
        "title": article.title,
        "subtitle": article.subtitle,
        "chapter": article.chapter,
        "section": article.section,
        "subsection": article.subsection,
        "annexe": article.annexe,
        "hierarchy_path": article.hierarchy_path,
        # Provenance (§33)
        "page_start": article.page_start,
        "page_end": article.page_end,
        "char_start": article.char_start,
        "char_end": article.char_end,
        "source_file": article.source_file or document.source.filename,
        "source_path": document.source.source_path,
        # Métadonnées documentaires reportées
        "document_title": metadata.title,
        "document_type": metadata.type.value,
        "document_number": metadata.number,
        "document_date": metadata.date,
        "document_status": metadata.status.value,
        "jurisdiction": metadata.jurisdiction,
        "language": metadata.language,
        "validation": document.validation.value,
        "warnings": list(article.warnings),
    }


def chunk_record(chunk: Chunk) -> dict:
    return chunk.to_dict()


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def export_documents_jsonl(
    documents: Sequence[Document], path: str | Path, include_pages: bool = True
) -> int:
    count = write_jsonl(path, (document_record(d, include_pages) for d in documents))
    logger.info("%d document(s) exporté(s) vers %s", count, path)
    return count


def export_articles_jsonl(documents: Sequence[Document], path: str | Path) -> int:
    count = write_jsonl(
        path,
        (article_record(d, article) for d in documents for article in d.articles),
    )
    logger.info("%d article(s) exporté(s) vers %s", count, path)
    return count


def export_chunks_jsonl(chunks: Sequence[Chunk], path: str | Path) -> int:
    count = write_jsonl(path, (chunk_record(c) for c in chunks))
    logger.info("%d fragment(s) exporté(s) vers %s", count, path)
    return count


def export_metadata_json(documents: Sequence[Document], path: str | Path) -> Path:
    """Métadonnées consolidées du corpus, confiances et preuves incluses."""
    payload = {
        "generated_at": utc_now_iso(),
        "document_count": len(documents),
        "documents": [
            {
                **document.metadata.to_dict(),
                "source_file": document.source.filename,
                "file_hash": document.source.file_hash,
                "article_count": len(document.articles),
                "validation": document.validation.value,
            }
            for document in documents
        ],
    }
    return write_json(path, payload)


def export_quality_report_json(documents: Sequence[Document], path: str | Path) -> Path:
    """Rapport qualité agrégé du corpus (§15)."""
    reports = [d.quality for d in documents if d.quality]
    scores = [report.score for report in reports]
    by_status: dict[str, int] = {}
    for report in reports:
        key = report.status.value
        by_status[key] = by_status.get(key, 0) + 1

    payload = {
        "generated_at": utc_now_iso(),
        "document_count": len(documents),
        "reported_count": len(reports),
        "average_score": round(sum(scores) / len(scores), 4) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "by_status": by_status,
        "documents_requiring_review": [
            report.document_id
            for report in reports
            if report.status is not QualityStatus.OK
        ],
        "reports": [report.to_dict() for report in reports],
    }
    return write_json(path, payload)


def export_articles_csv(documents: Sequence[Document], path: str | Path) -> int:
    """Export CSV des articles (colonnes plates, pour tableur)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "article_id", "document_id", "article_number", "document_title",
        "document_type", "document_number", "document_date", "title", "chapter",
        "section", "page_start", "page_end", "source_file", "validation", "text",
    ]
    count = 0
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for document in documents:
            for article in document.articles:
                writer.writerow(article_record(document, article))
                count += 1
    logger.info("%d article(s) exporté(s) en CSV vers %s", count, target)
    return count


def export_run_report(report: Any, path: str | Path) -> Path:
    """Bilan d'exécution : réussis / à vérifier / échoués (§26)."""
    payload = asdict(report) if is_dataclass(report) and not isinstance(report, type) else report
    return write_json(path, payload)


def export_by_category(
    documents: Sequence[Document], directory: str | Path
) -> Path:
    """Range chaque document traité dans un sous-dossier par catégorie.

    Produit, pour chaque document ::

        data/traites/
            accords/
                accord_2023_150.json   (fiche complète : métadonnées, articles)
                accord_2023_150.txt    (texte nettoyé, page par page)
            arretes/
                ...

    C'est la vue « par lot » du corpus : les exports globaux (JSONL, SQLite)
    restent la référence pour l'exploitation, mais un relecteur qui travaille
    catégorie par catégorie retrouve ici exactement les documents de son lot,
    sans requête. Les fichiers sont **réécrits** à chaque exécution : la base
    SQLite reste la seule mémoire des décisions.
    """
    base = Path(directory)
    for document in documents:
        category = (document.source.category or "autres") if document.source else "autres"
        folder = base / category
        folder.mkdir(parents=True, exist_ok=True)

        # Si un passage précédent avait rangé ce document dans une autre
        # catégorie, ses anciens fichiers y resteraient : deux copies du même
        # document dans deux dossiers, dont une périmée. On ne retire que les
        # fichiers portant exactement son identifiant.
        for stale in base.glob(f"*/{document.document_id}.*"):
            if stale.parent != folder:
                stale.unlink(missing_ok=True)

        record = document_record(document)
        write_json(folder / f"{document.document_id}.json", record)

        pages = document.pages or []
        texte = "\n\n".join(
            f"--- page {page.page} ---\n{page.text}" for page in pages
        )
        (folder / f"{document.document_id}.txt").write_text(texte, encoding="utf-8")

    logger.info(
        "Documents traités rangés par catégorie dans %s (%d document(s)).",
        base,
        len(documents),
    )
    return base


def export_slice(
    documents: Sequence[Document],
    config: Config,
    first: bool,
    output_dir: str | Path | None = None,
) -> dict[str, int]:
    """Verse une tranche de corpus dans les exports, sans écraser les autres.

    Exporter un gros corpus d'un seul tenant suppose de le tenir entier en
    mémoire — ce qui a coûté quatre heures de calcul sur le lot 2 du corpus
    SGG, le noyau ayant tué le processus à 5,1 Go au moment de l'écriture.

    Cette fonction écrit une tranche et l'oublie. Le premier appel remet les
    fichiers à zéro ; les suivants ajoutent à la suite. La base SQLite, elle,
    est naturellement incrémentale.

    Les agrégats — ``metadata.json``, ``quality_report.json`` — ne sont pas
    écrits ici : ils demandent le corpus entier et se régénèrent en fin de
    parcours, à partir de la base.

    Args:
        first: vrai pour la première tranche, qui repart de fichiers vides.

    Returns:
        Le nombre d'enregistrements ajoutés, par nom logique.
    """
    destination = Path(output_dir) if output_dir else config.path("exports")
    destination.mkdir(parents=True, exist_ok=True)
    formats = {str(f).lower() for f in config.get("export.formats", ["jsonl", "sqlite", "json"])}
    include_pages = bool(config.get("export.include_page_text", True))
    ajout = not first
    ecrits: dict[str, int] = {}

    if "jsonl" in formats:
        ecrits["documents"] = write_jsonl(
            destination / "documents.jsonl",
            (document_record(d, include_pages) for d in documents),
            append=ajout,
        )
        ecrits["articles"] = write_jsonl(
            destination / "articles.jsonl",
            (article_record(d, a) for d in documents for a in d.articles),
            append=ajout,
        )

    if "sqlite" in formats:
        from bldp.core.storage.sqlite_store import LegalDatabase

        with LegalDatabase(config.path("database")) as database:
            database.save_documents(documents, include_pages=include_pages)
        ecrits["sqlite"] = len(documents)

    if bool(config.get("export.by_category", True)):
        export_by_category(documents, config.path("traites"))
        ecrits["traites"] = len(documents)

    return ecrits


def export_aggregates(
    documents: Iterable[Document], config: Config, output_dir: str | Path | None = None
) -> dict[str, str]:
    """Écrit les exports qui exigent le corpus entier, en le parcourant.

    ``metadata.json`` et ``quality_report.json`` récapitulent tout le corpus :
    impossible de les écrire par tranches. Mais leur contenu est **léger** —
    une fiche par document, sans le texte des pages — et se construit donc en
    parcourant la base sans jamais la charger d'un bloc.
    """
    destination = Path(output_dir) if output_dir else config.path("exports")
    destination.mkdir(parents=True, exist_ok=True)
    formats = {str(f).lower() for f in config.get("export.formats", ["jsonl", "sqlite", "json"])}
    produced: dict[str, str] = {}
    if "json" not in formats:
        return produced

    fiches: list[dict] = []
    qualites: list[Document] = []
    for document in documents:
        fiches.append(
            {
                **document.metadata.to_dict(),
                "source_file": document.source.filename,
                "file_hash": document.source.file_hash,
                "article_count": len(document.articles),
                "validation": document.validation.value,
            }
        )
        # Seul le rapport qualité est retenu, pas le document : quelques
        # centaines d'octets au lieu de plusieurs centaines de kilo-octets.
        allege = Document(
            document_id=document.document_id,
            source=document.source,
            metadata=document.metadata,
            quality=document.quality,
            validation=document.validation,
        )
        qualites.append(allege)

    chemin_metadonnees = destination / "metadata.json"
    write_json(
        chemin_metadonnees,
        {
            "generated_at": utc_now_iso(),
            "document_count": len(fiches),
            "documents": fiches,
        },
    )
    produced["metadata_json"] = str(chemin_metadonnees)

    chemin_qualite = destination / "quality_report.json"
    export_quality_report_json(qualites, chemin_qualite)
    produced["quality_report_json"] = str(chemin_qualite)
    return produced


def export_all(
    documents: Sequence[Document],
    config: Config,
    chunks: Sequence[Chunk] = (),
    output_dir: str | Path | None = None,
) -> dict[str, str]:
    """Produit tous les exports demandés par la configuration.

    Returns:
        Le chemin de chaque fichier produit, par nom logique.
    """
    destination = Path(output_dir) if output_dir else config.path("exports")
    destination.mkdir(parents=True, exist_ok=True)
    formats = {str(f).lower() for f in config.get("export.formats", ["jsonl", "sqlite", "json"])}
    include_pages = bool(config.get("export.include_page_text", True))
    produced: dict[str, str] = {}

    if "jsonl" in formats:
        documents_path = destination / "documents.jsonl"
        articles_path = destination / "articles.jsonl"
        export_documents_jsonl(documents, documents_path, include_pages)
        export_articles_jsonl(documents, articles_path)
        produced["documents_jsonl"] = str(documents_path)
        produced["articles_jsonl"] = str(articles_path)
        if chunks:
            chunks_path = destination / "chunks.jsonl"
            export_chunks_jsonl(chunks, chunks_path)
            produced["chunks_jsonl"] = str(chunks_path)

    if "json" in formats:
        metadata_path = destination / "metadata.json"
        quality_path = destination / "quality_report.json"
        export_metadata_json(documents, metadata_path)
        export_quality_report_json(documents, quality_path)
        produced["metadata_json"] = str(metadata_path)
        produced["quality_report_json"] = str(quality_path)

    if "csv" in formats:
        csv_path = destination / "articles.csv"
        export_articles_csv(documents, csv_path)
        produced["articles_csv"] = str(csv_path)

    if "sqlite" in formats:
        from bldp.core.storage.sqlite_store import LegalDatabase

        database_path = config.path("database")
        with LegalDatabase(database_path) as database:
            database.save_documents(documents, include_pages=include_pages)
            if chunks:
                database.save_chunks(chunks)
        produced["sqlite"] = str(database_path)

    if bool(config.get("export.by_category", True)):
        produced["traites"] = str(
            export_by_category(documents, config.path("traites"))
        )

    if "parquet" in formats:
        logger.warning(
            "Le format Parquet est prévu pour une version ultérieure : export ignoré."
        )

    return produced


def load_documents_jsonl(path: str | Path) -> Iterable[dict]:
    """Relit un export ``documents.jsonl`` (utile aux tests et à la reprise)."""
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)
