"""Base structurée SQLite (§17 du cahier des charges).

Le schéma matérialise la chaîne de traçabilité du §33 :

.. code-block:: text

    documents ─┬─ pages          (texte brut + nettoyé, par page)
               ├─ structure      (titres, chapitres, sections…)
               ├─ articles ── alineas
               ├─ relations      (modifie / abroge / remplace…)
               ├─ duplicates     (doublons marqués, jamais supprimés)
               ├─ quality_reports ── quality_issues
               └─ chunks ── embeddings

Deux principes de conception :

* **rien n'est perdu** : les pages conservent le texte brut *et* le texte
  nettoyé, ce qui permet de rejouer ou de contester le nettoyage ;
* **rien n'est écrasé en silence** : réécrire un document remplace ses lignes
  filles de façon transactionnelle, et la décision de validation humaine
  survit à un retraitement.

SQLite suffit au MVP : la base est un simple fichier, sans serveur, ce qui
respecte la contrainte d'exécution locale sur une machine à 16 Go.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from bldp.logging_setup import get_logger
from bldp.models import (
    Article,
    Chunk,
    Document,
    DocumentMetadata,
    DocumentType,
    DuplicateLink,
    LegalRelation,
    LegalStatus,
    Page,
    QualityReport,
    StructureNode,
    ValidationStatus,
)

logger = get_logger("storage.sqlite")

#: Version du schéma, pour détecter une base créée par une version antérieure.
SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_info (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Un document tel qu'importé et traité.
CREATE TABLE IF NOT EXISTS documents (
    document_id       TEXT PRIMARY KEY,
    title             TEXT,
    type              TEXT,
    number            TEXT,
    date              TEXT,
    jurisdiction      TEXT,
    authority         TEXT,
    legal_domain      TEXT,
    language          TEXT,
    source            TEXT,
    source_url        TEXT,
    retrieved_at      TEXT,
    version           TEXT,
    status            TEXT,
    -- Provenance : l'original n'est jamais modifié, on garde son chemin.
    source_path       TEXT,
    raw_path          TEXT,
    filename          TEXT,
    category          TEXT,
    file_hash         TEXT,
    text_hash         TEXT,
    size_bytes        INTEGER,
    page_count        INTEGER,
    extraction_method TEXT,
    ocr_required      INTEGER,
    ocr_confidence    REAL,
    validation        TEXT DEFAULT 'en_attente',
    validation_note   TEXT,
    processed_at      TEXT,
    pipeline_version  TEXT,
    metadata_json     TEXT,   -- confiance + preuves, conservées telles quelles
    errors_json       TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    document_id    TEXT NOT NULL,
    page           INTEGER NOT NULL,
    text           TEXT,
    raw_text       TEXT,
    char_count     INTEGER,
    method         TEXT,
    ocr_confidence REAL,
    warnings_json  TEXT,
    source_file    TEXT,
    PRIMARY KEY (document_id, page),
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS structure (
    node_id     TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    level       TEXT NOT NULL,
    number      TEXT,
    label       TEXT,
    heading     TEXT,
    page        INTEGER,
    char_start  INTEGER,
    char_end    INTEGER,
    parent_id   TEXT,
    depth       INTEGER,
    path_json   TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS articles (
    article_id     TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL,
    article_number TEXT NOT NULL,
    numeric_value  REAL,
    text           TEXT,
    label          TEXT,
    position       INTEGER,
    page_start     INTEGER,
    page_end       INTEGER,
    char_start     INTEGER,
    char_end       INTEGER,
    partie         TEXT,
    livre          TEXT,
    title          TEXT,
    subtitle       TEXT,
    chapter        TEXT,
    section        TEXT,
    subsection     TEXT,
    annexe         TEXT,
    hierarchy_json TEXT,
    alinea_count   INTEGER,
    warnings_json  TEXT,
    source_file    TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alineas (
    article_id TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    number     TEXT,
    text       TEXT,
    PRIMARY KEY (article_id, idx),
    FOREIGN KEY (article_id) REFERENCES articles(article_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS relations (
    relation_id        TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL,
    relation           TEXT NOT NULL,
    target_reference   TEXT,
    target_document_id TEXT,
    confidence         REAL,
    needs_review       INTEGER,
    article_id         TEXT,
    page               INTEGER,
    excerpt            TEXT,
    FOREIGN KEY (source_document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS duplicates (
    document_id  TEXT NOT NULL,
    duplicate_of TEXT NOT NULL,
    kind         TEXT NOT NULL,
    similarity   REAL,
    details      TEXT,
    PRIMARY KEY (document_id, duplicate_of, kind),
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quality_reports (
    document_id       TEXT PRIMARY KEY,
    score             REAL,
    ocr_quality       REAL,
    text_quality      REAL,
    structure_quality REAL,
    pages             INTEGER,
    empty_pages       INTEGER,
    duplicate_pages   INTEGER,
    missing_pages     INTEGER,
    articles_detected INTEGER,
    possible_errors   INTEGER,
    numbering_json    TEXT,
    status            TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quality_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    code        TEXT NOT NULL,
    severity    TEXT,
    message     TEXT,
    page        INTEGER,
    article_id  TEXT,
    count       INTEGER,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL,
    article_id     TEXT,
    article_number TEXT,
    text           TEXT,
    position       INTEGER,
    page           INTEGER,
    title          TEXT,
    chapter        TEXT,
    section        TEXT,
    hierarchy_json TEXT,
    char_start     INTEGER,
    char_end       INTEGER,
    strategy       TEXT,
    metadata_json  TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS embeddings (
    vector_id       TEXT PRIMARY KEY,
    chunk_id        TEXT NOT NULL,
    document_id     TEXT NOT NULL,
    article_id      TEXT,
    article_number  TEXT,
    embedding_model TEXT,
    dimension       INTEGER,
    vector_json     TEXT,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_articles_document  ON articles(document_id);
CREATE INDEX IF NOT EXISTS idx_articles_number    ON articles(document_id, numeric_value);
CREATE INDEX IF NOT EXISTS idx_pages_document     ON pages(document_id);
CREATE INDEX IF NOT EXISTS idx_structure_document ON structure(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document    ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_article     ON chunks(article_id);
CREATE INDEX IF NOT EXISTS idx_relations_source   ON relations(source_document_id);
CREATE INDEX IF NOT EXISTS idx_relations_target   ON relations(target_document_id);
CREATE INDEX IF NOT EXISTS idx_documents_hash     ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_texthash ON documents(text_hash);
CREATE INDEX IF NOT EXISTS idx_documents_status   ON documents(validation);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


class LegalDatabase:
    """Accès à la base SQLite du corpus.

    Utilisable comme gestionnaire de contexte ::

        with LegalDatabase("data/exports/legal_database.sqlite") as db:
            db.save_document(document)
    """

    def __init__(self, path: str | Path, create: bool = True) -> None:
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        # WAL : lectures concurrentes pendant l'écriture (utile à l'interface web).
        if str(self.path) != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        if create:
            self.create_schema()

    # -- cycle de vie --------------------------------------------------------

    def create_schema(self) -> None:
        """Crée les tables si elles n'existent pas et enregistre la version."""
        with self.connection:
            self.connection.executescript(SCHEMA)
            self.connection.execute(
                "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM schema_info WHERE key = 'version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LegalDatabase":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaction explicite : tout passe, ou rien ne passe."""
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    # -- écriture ------------------------------------------------------------

    def save_document(self, document: Document, include_pages: bool = True) -> None:
        """Enregistre (ou remplace) un document et tout ce qui en dépend.

        L'opération est atomique. La décision de validation humaine déjà
        enregistrée est **préservée** : retraiter un document ne doit pas
        annuler le travail d'un relecteur (§16).
        """
        previous = self.get_validation(document.document_id)

        with self.transaction() as connection:
            self._delete_children(connection, document.document_id)
            self._insert_document(connection, document, previous)
            if include_pages:
                self._insert_pages(connection, document.document_id, document.pages)
            self._insert_structure(connection, document.document_id, document.structure)
            self._insert_articles(connection, document.document_id, document.articles)
            self._insert_relations(connection, document.document_id, document.relations)
            self._insert_duplicates(connection, document.document_id, document.duplicates)
            if document.quality:
                self._insert_quality(connection, document.document_id, document.quality)

        logger.info(
            "%s enregistré : %d page(s), %d article(s)",
            document.document_id,
            len(document.pages),
            len(document.articles),
        )

    def save_documents(self, documents: Iterable[Document], include_pages: bool = True) -> int:
        """Enregistre plusieurs documents ; un échec n'annule pas les autres (§26)."""
        saved = 0
        for document in documents:
            try:
                self.save_document(document, include_pages=include_pages)
                saved += 1
            except sqlite3.Error as exc:
                logger.error("Échec d'enregistrement de %s : %s", document.document_id, exc)
        return saved

    def save_chunks(self, chunks: Sequence[Chunk]) -> int:
        """Enregistre les fragments prêts pour l'indexation."""
        if not chunks:
            return 0
        with self.transaction() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO chunks (
                       chunk_id, document_id, article_id, article_number, text,
                       position, page, title, chapter, section, hierarchy_json,
                       char_start, char_end, strategy, metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        chunk.chunk_id, chunk.document_id, chunk.article_id,
                        chunk.article_number, chunk.text, chunk.position, chunk.page,
                        chunk.title, chunk.chapter, chunk.section,
                        _json(chunk.hierarchy_path), chunk.char_start, chunk.char_end,
                        chunk.strategy, _json(chunk.metadata),
                    )
                    for chunk in chunks
                ],
            )
        return len(chunks)

    def save_embeddings(self, records: Sequence[Any]) -> int:
        """Enregistre les vecteurs et leurs métadonnées d'origine (§19)."""
        if not records:
            return 0
        with self.transaction() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO embeddings (
                       vector_id, chunk_id, document_id, article_id, article_number,
                       embedding_model, dimension, vector_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        record.vector_id, record.chunk_id, record.document_id,
                        record.article_id, record.article_number,
                        record.embedding_model, record.dimension, _json(record.vector),
                    )
                    for record in records
                ],
            )
        return len(records)

    def set_validation(
        self, document_id: str, status: ValidationStatus, note: str = ""
    ) -> None:
        """Enregistre la décision d'un relecteur humain (§16)."""
        with self.transaction() as connection:
            connection.execute(
                "UPDATE documents SET validation = ?, validation_note = ? WHERE document_id = ?",
                (_enum(status), note, document_id),
            )

    def delete_document(self, document_id: str) -> None:
        """Supprime un document et ses dépendances (cascade)."""
        with self.transaction() as connection:
            connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))

    # -- lecture -------------------------------------------------------------

    def get_validation(self, document_id: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            "SELECT validation, validation_note FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return (row["validation"], row["validation_note"] or "") if row else None

    def get_document_row(self, document_id: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()

    def list_documents(
        self,
        validation: str | None = None,
        document_type: str | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM documents"
        clauses: list[str] = []
        params: list[Any] = []
        if validation:
            clauses.append("validation = ?")
            params.append(_enum(validation))
        if document_type:
            clauses.append("type = ?")
            params.append(_enum(document_type))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY document_id"
        if limit:
            query += f" LIMIT {int(limit)}"
        return self.connection.execute(query, params).fetchall()

    def get_articles(self, document_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM articles WHERE document_id = ? ORDER BY position",
            (document_id,),
        ).fetchall()

    def get_article(self, article_id: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM articles WHERE article_id = ?", (article_id,)
        ).fetchone()

    def get_alineas(self, article_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM alineas WHERE article_id = ? ORDER BY idx", (article_id,)
        ).fetchall()

    def get_pages(self, document_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM pages WHERE document_id = ? ORDER BY page", (document_id,)
        ).fetchall()

    def get_page(self, document_id: str, page: int) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM pages WHERE document_id = ? AND page = ?", (document_id, page)
        ).fetchone()

    def get_structure(self, document_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM structure WHERE document_id = ? ORDER BY char_start",
            (document_id,),
        ).fetchall()

    def get_quality(self, document_id: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM quality_reports WHERE document_id = ?", (document_id,)
        ).fetchone()

    def get_relations(self, document_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM relations WHERE source_document_id = ? OR target_document_id = ?",
            (document_id, document_id),
        ).fetchall()

    def find_by_file_hash(self, file_hash: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT document_id, filename FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchall()

    def find_by_text_hash(self, text_hash: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT document_id, filename FROM documents WHERE text_hash = ?", (text_hash,)
        ).fetchall()

    def trace_article(self, article_id: str) -> Optional[dict]:
        """Remonte la chaîne complète d'un article jusqu'à sa source (§33).

        Renvoie l'article, son document, sa page d'origine et le texte de cette
        page — de quoi vérifier à la main que l'extraction est fidèle.
        """
        article = self.get_article(article_id)
        if not article:
            return None
        document = self.get_document_row(article["document_id"])
        page = self.get_page(article["document_id"], article["page_start"])
        return {
            "article": dict(article),
            "alineas": [dict(row) for row in self.get_alineas(article_id)],
            "document": dict(document) if document else None,
            "page": dict(page) if page else None,
            "source_path": document["source_path"] if document else None,
        }

    def search_articles(self, query: str, limit: int = 50) -> list[sqlite3.Row]:
        """Recherche plein texte simple (LIKE), suffisante pour le MVP."""
        return self.connection.execute(
            "SELECT article_id, document_id, article_number, "
            "substr(text, 1, 300) AS extrait FROM articles "
            "WHERE text LIKE ? ORDER BY document_id, position LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()

    def stats(self) -> dict[str, Any]:
        """Compteurs globaux du corpus."""
        counts = {}
        for table in (
            "documents", "pages", "structure", "articles", "alineas",
            "relations", "duplicates", "chunks", "embeddings",
        ):
            counts[table] = self.connection.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]

        by_validation = {
            row["validation"]: row["n"]
            for row in self.connection.execute(
                "SELECT validation, COUNT(*) AS n FROM documents GROUP BY validation"
            )
        }
        by_type = {
            row["type"]: row["n"]
            for row in self.connection.execute(
                "SELECT type, COUNT(*) AS n FROM documents GROUP BY type"
            )
        }
        average = self.connection.execute(
            "SELECT AVG(score) AS s FROM quality_reports"
        ).fetchone()["s"]

        return {
            "counts": counts,
            "by_validation": by_validation,
            "by_type": by_type,
            "average_quality_score": round(average, 4) if average is not None else None,
            "schema_version": self.schema_version(),
        }

    # -- internes ------------------------------------------------------------

    @staticmethod
    def _delete_children(connection: sqlite3.Connection, document_id: str) -> None:
        """Vide les lignes filles avant réécriture (idempotence)."""
        for table, column in (
            ("pages", "document_id"),
            ("structure", "document_id"),
            ("articles", "document_id"),   # cascade sur alineas
            ("relations", "source_document_id"),
            ("duplicates", "document_id"),
            ("quality_reports", "document_id"),
            ("quality_issues", "document_id"),
        ):
            connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (document_id,))

    @staticmethod
    def _insert_document(
        connection: sqlite3.Connection,
        document: Document,
        previous_validation: tuple[str, str] | None,
    ) -> None:
        metadata = document.metadata
        source = document.source
        analysis = document.analysis
        extraction = document.extraction

        # La décision humaine prime sur la valeur du nouvel objet.
        validation = _enum(document.validation)
        note = document.validation_note
        if previous_validation and previous_validation[0] != ValidationStatus.PENDING.value:
            validation, note = previous_validation

        ocr_scores = [
            page.ocr_confidence for page in document.pages if page.ocr_confidence is not None
        ]
        connection.execute(
            """INSERT OR REPLACE INTO documents (
                   document_id, title, type, number, date, jurisdiction, authority,
                   legal_domain, language, source, source_url, retrieved_at, version,
                   status, source_path, raw_path, filename, category, file_hash,
                   text_hash, size_bytes, page_count, extraction_method, ocr_required,
                   ocr_confidence, validation, validation_note, processed_at,
                   pipeline_version, metadata_json, errors_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document.document_id,
                metadata.title,
                _enum(metadata.type),
                metadata.number,
                metadata.date,
                metadata.jurisdiction,
                metadata.authority,
                metadata.legal_domain,
                metadata.language,
                metadata.source,
                metadata.source_url,
                metadata.retrieved_at,
                metadata.version,
                _enum(metadata.status),
                source.source_path,
                source.raw_path,
                source.filename,
                source.category,
                source.file_hash,
                document.text_hash,
                source.size_bytes,
                len(document.pages) or (analysis.pages if analysis else 0),
                _enum(extraction.method) if extraction else None,
                int(analysis.ocr_required) if analysis else None,
                round(sum(ocr_scores) / len(ocr_scores), 4) if ocr_scores else None,
                validation,
                note,
                document.processed_at,
                document.pipeline_version,
                _json(
                    {
                        "confidence": metadata.confidence,
                        "evidence": metadata.evidence,
                        "warnings": metadata.warnings,
                    }
                ),
                _json(document.errors),
            ),
        )

    @staticmethod
    def _insert_pages(
        connection: sqlite3.Connection, document_id: str, pages: Sequence[Page]
    ) -> None:
        connection.executemany(
            """INSERT OR REPLACE INTO pages (
                   document_id, page, text, raw_text, char_count, method,
                   ocr_confidence, warnings_json, source_file)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    document_id, page.page, page.text, page.raw_text, page.char_count,
                    _enum(page.method), page.ocr_confidence, _json(page.warnings),
                    page.source_file,
                )
                for page in pages
            ],
        )

    @staticmethod
    def _insert_structure(
        connection: sqlite3.Connection, document_id: str, nodes: Sequence[StructureNode]
    ) -> None:
        connection.executemany(
            """INSERT OR REPLACE INTO structure (
                   node_id, document_id, level, number, label, heading, page,
                   char_start, char_end, parent_id, depth, path_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    node.node_id, document_id, _enum(node.level), node.number, node.label,
                    node.heading, node.page, node.char_start, node.char_end,
                    node.parent_id, node.depth, _json(node.path),
                )
                for node in nodes
            ],
        )

    @staticmethod
    def _insert_articles(
        connection: sqlite3.Connection, document_id: str, articles: Sequence[Article]
    ) -> None:
        connection.executemany(
            """INSERT OR REPLACE INTO articles (
                   article_id, document_id, article_number, numeric_value, text, label,
                   position, page_start, page_end, char_start, char_end, partie, livre,
                   title, subtitle, chapter, section, subsection, annexe,
                   hierarchy_json, alinea_count, warnings_json, source_file)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    article.article_id, document_id, article.article_number,
                    article.numeric_value, article.text, article.label, article.position,
                    article.page_start, article.page_end, article.char_start,
                    article.char_end, article.partie, article.livre, article.title,
                    article.subtitle, article.chapter, article.section,
                    article.subsection, article.annexe, _json(article.hierarchy_path),
                    len(article.alineas), _json(article.warnings), article.source_file,
                )
                for article in articles
            ],
        )
        connection.executemany(
            "INSERT OR REPLACE INTO alineas (article_id, idx, number, text) VALUES (?,?,?,?)",
            [
                (article.article_id, alinea.index, alinea.number, alinea.text)
                for article in articles
                for alinea in article.alineas
            ],
        )

    @staticmethod
    def _insert_relations(
        connection: sqlite3.Connection, document_id: str, relations: Sequence[LegalRelation]
    ) -> None:
        connection.executemany(
            """INSERT OR REPLACE INTO relations (
                   relation_id, source_document_id, relation, target_reference,
                   target_document_id, confidence, needs_review, article_id, page, excerpt)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    relation.relation_id, document_id, _enum(relation.relation),
                    relation.target_reference, relation.target_document_id,
                    relation.confidence, int(relation.needs_review), relation.article_id,
                    relation.page, relation.excerpt,
                )
                for relation in relations
            ],
        )

    @staticmethod
    def _insert_duplicates(
        connection: sqlite3.Connection, document_id: str, duplicates: Sequence[DuplicateLink]
    ) -> None:
        connection.executemany(
            """INSERT OR REPLACE INTO duplicates (
                   document_id, duplicate_of, kind, similarity, details)
               VALUES (?,?,?,?,?)""",
            [
                (document_id, link.duplicate_of, link.kind, link.similarity, link.details)
                for link in duplicates
            ],
        )

    @staticmethod
    def _insert_quality(
        connection: sqlite3.Connection, document_id: str, report: QualityReport
    ) -> None:
        connection.execute(
            """INSERT OR REPLACE INTO quality_reports (
                   document_id, score, ocr_quality, text_quality, structure_quality,
                   pages, empty_pages, duplicate_pages, missing_pages,
                   articles_detected, possible_errors, numbering_json, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id, report.score, report.ocr_quality, report.text_quality,
                report.structure_quality, report.pages, report.empty_pages,
                report.duplicate_pages, report.missing_pages, report.articles_detected,
                report.possible_errors, _json(report.numbering_gaps), _enum(report.status),
            ),
        )
        connection.executemany(
            """INSERT INTO quality_issues (
                   document_id, code, severity, message, page, article_id, count)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (
                    document_id, issue.code, issue.severity, issue.message,
                    issue.page, issue.article_id, issue.count,
                )
                for issue in report.issues
            ],
        )


def rebuild_metadata(row: sqlite3.Row) -> DocumentMetadata:
    """Reconstruit un objet métadonnées depuis une ligne ``documents``."""
    extra = json.loads(row["metadata_json"] or "{}")
    return DocumentMetadata(
        document_id=row["document_id"],
        title=row["title"],
        type=DocumentType(row["type"]) if row["type"] else DocumentType.INCONNU,
        number=row["number"],
        date=row["date"],
        jurisdiction=row["jurisdiction"] or "Benin",
        authority=row["authority"],
        legal_domain=row["legal_domain"],
        language=row["language"] or "fr",
        source=row["source"],
        source_url=row["source_url"],
        retrieved_at=row["retrieved_at"],
        version=row["version"] or "1",
        status=LegalStatus(row["status"]) if row["status"] else LegalStatus.INCONNU,
        confidence=extra.get("confidence", {}),
        evidence=extra.get("evidence", {}),
        warnings=extra.get("warnings", []),
    )
