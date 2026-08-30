"""Module 12 — base vectorielle (§4 du cahier des charges).

FAISS est la cible du MVP ; Qdrant est prévu pour une version ultérieure.
L'interface :class:`VectorStore` est volontairement minimale et agnostique,
afin qu'un autre moteur puisse être branché sans toucher au reste du pipeline.

Comme les embeddings, ce module est **optionnel** : le corpus reste complet et
exportable sans lui. L'index vit sur disque à côté d'un fichier de métadonnées
JSON qui conserve, pour chaque vecteur, son article et sa page d'origine — sans
quoi un résultat de recherche serait inexploitable pour citer une source (§33).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import EmbeddingRecord

logger = get_logger("vectorstore")

#: Suffixe du fichier de métadonnées accompagnant l'index.
METADATA_SUFFIX = ".meta.json"


class VectorStoreUnavailableError(RuntimeError):
    """Le moteur d'index vectoriel n'est pas installé."""


class VectorStoreError(RuntimeError):
    """L'index existe mais l'opération a échoué."""


def faiss_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faiss") is not None


def check_vectorstore_ready(config: Config) -> tuple[bool, list[str]]:
    """Vérifie que l'indexation vectorielle est possible et autorisée."""
    if not config.get("vectorstore.enabled", False):
        return False, ["la base vectorielle est désactivée (vectorstore.enabled=false)"]

    backend = str(config.get("vectorstore.backend", "faiss")).lower()
    if backend == "qdrant":
        return False, ["le backend Qdrant est prévu pour une version ultérieure (§4)"]
    if backend != "faiss":
        return False, [f"backend d'index inconnu : {backend!r}"]
    if not faiss_available():
        return False, ['faiss n\'est pas installé — pip install -e ".[faiss]"']
    return True, []


@dataclass
class SearchHit:
    """Un résultat de recherche, avec de quoi citer sa source."""

    rank: int
    score: float
    chunk_id: str
    document_id: str
    article_id: Optional[str]
    article_number: Optional[str]
    page: Optional[int]
    text: str
    metadata: dict

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "score": self.score,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "article_id": self.article_id,
            "article_number": self.article_number,
            "page": self.page,
            "text": self.text,
            "metadata": self.metadata,
        }


class FaissStore:
    """Index vectoriel FAISS, persisté sur disque.

    L'index ne stocke que des vecteurs ; les métadonnées vivent dans un fichier
    JSON parallèle, aligné sur l'ordre d'insertion. Les deux sont écrits et
    relus ensemble : un index sans ses métadonnées serait inutilisable.
    """

    def __init__(self, path: str | Path, dimension: int, metric: str = "cosine") -> None:
        self.path = Path(path)
        self.dimension = dimension
        self.metric = metric
        self._index: Any = None
        self.entries: list[dict] = []

    # -- construction --------------------------------------------------------

    @staticmethod
    def _import_faiss() -> Any:
        try:
            import faiss  # type: ignore

            return faiss
        except ImportError as exc:
            raise VectorStoreUnavailableError(
                'faiss n\'est pas installé. Installez-le avec : pip install -e ".[faiss]"'
            ) from exc

    def _new_index(self) -> Any:
        faiss = self._import_faiss()
        # Avec des vecteurs normalisés, le produit scalaire *est* le cosinus.
        if self.metric == "cosine":
            return faiss.IndexFlatIP(self.dimension)
        return faiss.IndexFlatL2(self.dimension)

    @property
    def index(self) -> Any:
        if self._index is None:
            self._index = self._new_index()
        return self._index

    def __len__(self) -> int:
        return len(self.entries)

    # -- écriture ------------------------------------------------------------

    def add(self, records: Sequence[EmbeddingRecord]) -> int:
        """Ajoute des vecteurs et leurs métadonnées."""
        if not records:
            return 0

        import numpy as np

        vectors = np.asarray([record.vector for record in records], dtype="float32")
        if vectors.shape[1] != self.dimension:
            raise VectorStoreError(
                f"dimension incompatible : index={self.dimension}, "
                f"vecteurs={vectors.shape[1]}"
            )
        if self.metric == "cosine":
            faiss = self._import_faiss()
            faiss.normalize_L2(vectors)

        self.index.add(vectors)
        self.entries.extend(
            {
                "vector_id": record.vector_id,
                "chunk_id": record.chunk_id,
                "document_id": record.document_id,
                "article_id": record.article_id,
                "article_number": record.article_number,
                "embedding_model": record.embedding_model,
                "text": record.text,
            }
            for record in records
        )
        logger.info("%d vecteur(s) ajouté(s) à l'index (total : %d)", len(records), len(self))
        return len(records)

    def save(self) -> Path:
        """Écrit l'index et ses métadonnées côte à côte."""
        faiss = self._import_faiss()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.path))
        metadata_path = self.path.with_suffix(self.path.suffix + METADATA_SUFFIX)
        with metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "dimension": self.dimension,
                    "metric": self.metric,
                    "count": len(self.entries),
                    "entries": self.entries,
                },
                handle,
                ensure_ascii=False,
            )
        logger.info("Index vectoriel écrit : %s (%d vecteurs)", self.path, len(self))
        return self.path

    # -- lecture -------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "FaissStore":
        """Recharge un index et ses métadonnées."""
        faiss = cls._import_faiss()
        index_path = Path(path)
        metadata_path = index_path.with_suffix(index_path.suffix + METADATA_SUFFIX)

        if not index_path.exists():
            raise VectorStoreError(f"Index introuvable : {index_path}")
        if not metadata_path.exists():
            raise VectorStoreError(
                f"Métadonnées d'index introuvables : {metadata_path}. "
                "Un index sans métadonnées ne permet pas de citer ses sources."
            )

        with metadata_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        store = cls(index_path, int(payload["dimension"]), payload.get("metric", "cosine"))
        store._index = faiss.read_index(str(index_path))
        store.entries = payload.get("entries", [])

        if store._index.ntotal != len(store.entries):
            raise VectorStoreError(
                f"index et métadonnées désynchronisés : {store._index.ntotal} vecteurs "
                f"pour {len(store.entries)} entrées."
            )
        return store

    def search(self, query_vector: Sequence[float], top_k: int = 5) -> list[SearchHit]:
        """Recherche les ``top_k`` fragments les plus proches."""
        if not len(self):
            return []

        import numpy as np

        vector = np.asarray([list(query_vector)], dtype="float32")
        if vector.shape[1] != self.dimension:
            raise VectorStoreError(
                f"dimension de requête incompatible : index={self.dimension}, "
                f"requête={vector.shape[1]}"
            )
        if self.metric == "cosine":
            faiss = self._import_faiss()
            faiss.normalize_L2(vector)

        scores, indices = self.index.search(vector, min(top_k, len(self)))
        hits: list[SearchHit] = []
        for rank, (score, position) in enumerate(zip(scores[0], indices[0]), start=1):
            if position < 0:
                continue
            entry = self.entries[int(position)]
            hits.append(
                SearchHit(
                    rank=rank,
                    score=float(score),
                    chunk_id=entry["chunk_id"],
                    document_id=entry["document_id"],
                    article_id=entry.get("article_id"),
                    article_number=entry.get("article_number"),
                    page=entry.get("page"),
                    text=entry.get("text", ""),
                    metadata=entry,
                )
            )
        return hits


def build_store(config: Config, dimension: int) -> FaissStore:
    """Construit l'index décrit par la configuration."""
    ready, problems = check_vectorstore_ready(config)
    if not ready:
        raise VectorStoreUnavailableError(" ; ".join(problems))
    return FaissStore(
        path=config.get("vectorstore.index_path", "data/embeddings/faiss.index"),
        dimension=dimension,
        metric=str(config.get("vectorstore.metric", "cosine")),
    )


def index_embeddings(records: Sequence[EmbeddingRecord], config: Config) -> Optional[Path]:
    """Construit et enregistre l'index à partir d'un lot de vecteurs.

    Renvoie ``None`` — avec un avertissement — lorsque l'indexation n'est pas
    disponible : l'absence d'index ne doit jamais faire échouer un pipeline
    dont le corpus est par ailleurs complet.
    """
    if not records:
        return None

    ready, problems = check_vectorstore_ready(config)
    if not ready:
        logger.warning("Indexation vectorielle ignorée : %s", " ; ".join(problems))
        return None

    store = FaissStore(
        path=config.get("vectorstore.index_path", "data/embeddings/faiss.index"),
        dimension=records[0].dimension,
        metric=str(config.get("vectorstore.metric", "cosine")),
    )
    store.add(records)
    return store.save()
