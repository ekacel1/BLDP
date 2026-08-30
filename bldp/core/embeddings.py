"""Module 11 — embeddings (§19 du cahier des charges).

Module **indépendant et optionnel** : le pipeline produit un corpus complet et
exploitable sans jamais charger de modèle. ``embeddings.enabled`` vaut ``false``
par défaut, conformément au §4 (« l'utilisation des embeddings doit être
optionnelle dans le pipeline »).

Le modèle tourne localement (Sentence Transformers, CPU) : aucun document n'est
envoyé à un service externe (§27). Chaque vecteur conserve les métadonnées
d'origine de son fragment, de sorte qu'un résultat de recherche vectorielle
puisse toujours être ramené à son article et à sa page (§19).
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import Chunk, EmbeddingRecord

logger = get_logger("embeddings")

#: Modèle par défaut : multilingue, léger, adapté au français et à une machine
#: sans GPU disposant de 16 Go de RAM.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingsUnavailableError(RuntimeError):
    """``sentence-transformers`` n'est pas installé."""


class EmbeddingError(RuntimeError):
    """Le modèle est disponible mais l'encodage a échoué."""


def embeddings_available() -> bool:
    """Vrai si la dépendance d'embeddings est installée."""
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


def check_embeddings_ready(config: Config) -> tuple[bool, list[str]]:
    """Vérifie que la génération d'embeddings est possible et autorisée."""
    problems: list[str] = []
    if not config.get("embeddings.enabled", False):
        return False, ["les embeddings sont désactivés (embeddings.enabled=false)"]
    if not embeddings_available():
        problems.append(
            "sentence-transformers n'est pas installé — "
            'pip install -e ".[embeddings]"'
        )
    return not problems, problems


class EmbeddingModel:
    """Enveloppe autour d'un modèle Sentence Transformers.

    Le modèle est chargé **paresseusement**, au premier encodage : importer ce
    module ne doit jamais déclencher le téléchargement d'un modèle.
    """

    def __init__(
        self,
        name: str = DEFAULT_MODEL,
        device: str = "cpu",
        normalize: bool = True,
        batch_size: int = 32,
    ) -> None:
        self.name = name
        self.device = device
        self.normalize = normalize
        self.batch_size = batch_size
        self._model: Any = None

    @property
    def model(self) -> Any:
        if self._model is None:
            self._model = self._load()
        return self._model

    def _load(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingsUnavailableError(
                "sentence-transformers est requis pour générer des embeddings. "
                'Installez-le avec : pip install -e ".[embeddings]"'
            ) from exc

        logger.info("Chargement du modèle d'embedding %s (%s)", self.name, self.device)
        try:
            return SentenceTransformer(self.name, device=self.device)
        except Exception as exc:  # réseau absent, modèle introuvable…
            raise EmbeddingError(
                f"Impossible de charger le modèle {self.name} : {exc}. "
                "Vérifiez le nom du modèle, ou pré-téléchargez-le pour un "
                "fonctionnement hors ligne."
            ) from exc

    @property
    def dimension(self) -> int:
        """Dimension des vecteurs produits."""
        return int(self.model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str], show_progress: bool = False) -> list[list[float]]:
        """Encode une liste de textes en vecteurs."""
        if not texts:
            return []
        try:
            vectors = self.model.encode(
                list(texts),
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize,
                show_progress_bar=show_progress,
                convert_to_numpy=True,
            )
        except Exception as exc:
            raise EmbeddingError(f"Échec de l'encodage : {exc}") from exc
        return [[float(value) for value in vector] for vector in vectors]


def build_model(config: Config) -> EmbeddingModel:
    """Construit le modèle décrit par la configuration."""
    return EmbeddingModel(
        name=str(config.get("embeddings.model", DEFAULT_MODEL)),
        device=str(config.get("embeddings.device", "cpu")),
        normalize=bool(config.get("embeddings.normalize", True)),
        batch_size=int(config.get("embeddings.batch_size", 32)),
    )


def embed_chunks(
    chunks: Sequence[Chunk],
    config: Config,
    model: EmbeddingModel | None = None,
    show_progress: bool = False,
) -> list[EmbeddingRecord]:
    """Produit un vecteur par fragment, métadonnées conservées (§19).

    Args:
        chunks: fragments issus du module de chunking.
        config: configuration (section ``embeddings``).
        model: modèle déjà construit (évite un rechargement entre lots).
        show_progress: afficher une barre de progression.

    Raises:
        EmbeddingsUnavailableError: dépendance absente.
        EmbeddingError: échec du chargement ou de l'encodage.
    """
    if not chunks:
        return []

    model = model or build_model(config)
    texts = [chunk.text for chunk in chunks]
    vectors = model.encode(texts, show_progress=show_progress)
    dimension = len(vectors[0]) if vectors else 0

    records = [
        EmbeddingRecord(
            vector_id=f"{chunk.chunk_id}_vec",
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            embedding_model=model.name,
            dimension=dimension,
            article_id=chunk.article_id,
            article_number=chunk.article_number,
            text=chunk.text,
            vector=vector,
        )
        for chunk, vector in zip(chunks, vectors)
    ]

    logger.info(
        "%d vecteur(s) de dimension %d produits avec %s",
        len(records),
        dimension,
        model.name,
    )
    return records


def embed_query(query: str, config: Config, model: EmbeddingModel | None = None) -> list[float]:
    """Encode une requête de recherche avec le même modèle que le corpus."""
    model = model or build_model(config)
    vectors = model.encode([query])
    return vectors[0] if vectors else []


# ---------------------------------------------------------------------------
# Similarité (sans dépendance)
# ---------------------------------------------------------------------------


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Similarité cosinus entre deux vecteurs."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def brute_force_search(
    query_vector: Sequence[float],
    records: Sequence[EmbeddingRecord],
    top_k: int = 5,
) -> list[tuple[EmbeddingRecord, float]]:
    """Recherche exhaustive, sans index.

    Suffisante pour quelques milliers de fragments et utile pour vérifier un
    index FAISS ; au-delà, utiliser :mod:`bldp.core.vectorstore`.
    """
    scored = [(record, cosine_similarity(query_vector, record.vector)) for record in records]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def embeddings_summary(records: Iterable[EmbeddingRecord]) -> dict:
    """Résumé d'un lot de vecteurs."""
    records = list(records)
    if not records:
        return {"count": 0, "model": None, "dimension": None}
    return {
        "count": len(records),
        "model": records[0].embedding_model,
        "dimension": records[0].dimension,
        "documents": len({record.document_id for record in records}),
    }
