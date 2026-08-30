"""Tests du chunking (§20), des embeddings (§19) et de la base vectorielle (§4).

Les embeddings et FAISS étant optionnels et absents de la plupart des machines,
les tests portent principalement sur ce qui doit fonctionner **sans eux** :
le chunking, le caractère facultatif du module, et la dégradation propre.
"""

from __future__ import annotations

import pytest

from bldp.core import embeddings as embeddings_module
from bldp.core import vectorstore as vectorstore_module
from bldp.core.chunking import (
    chunk_article,
    chunk_document,
    chunk_documents,
    chunking_stats,
    pack_units,
    split_sentences,
)
from bldp.core.embeddings import (
    EmbeddingsUnavailableError,
    brute_force_search,
    check_embeddings_ready,
    cosine_similarity,
    embed_chunks,
    embeddings_summary,
)
from bldp.core.vectorstore import (
    VectorStoreUnavailableError,
    check_vectorstore_ready,
    index_embeddings,
)
from bldp.models import (
    Alinea,
    Article,
    Document,
    DocumentMetadata,
    DocumentType,
    EmbeddingRecord,
    ExtractionMethod,
    ExtractionResult,
    Page,
    SourceFile,
)
from bldp.utils import utc_now_iso


def make_document(
    document_id: str = "loi_2026_001",
    article_specs: list[tuple[str, str, list[str]]] | None = None,
    page_texts: list[str] | None = None,
) -> Document:
    """Fabrique un document. ``article_specs`` = [(numéro, texte, alinéas)]."""
    source = SourceFile(
        document_id=document_id,
        source_path=f"/input/{document_id}.pdf",
        filename=f"{document_id}.pdf",
        extension=".pdf",
        size_bytes=1000,
        file_hash="f" * 64,
        ingested_at=utc_now_iso(),
    )
    metadata = DocumentMetadata(
        document_id=document_id,
        title="Loi portant organisation du travail",
        type=DocumentType.LOI,
        number="2026-001",
        date="2026-02-10",
    )
    pages = [
        Page(document_id=document_id, page=i + 1, text=t, source_file=source.filename)
        for i, t in enumerate(page_texts or [])
    ]
    articles = []
    for position, (number, text, alineas) in enumerate(article_specs or []):
        articles.append(
            Article(
                article_id=f"{document_id}_article_{number}",
                document_id=document_id,
                article_number=number,
                text=text,
                label=f"Article {number}",
                position=position,
                page_start=1,
                page_end=1,
                title="TITRE I",
                chapter="CHAPITRE II",
                section="Section 1",
                hierarchy_path=["TITRE I", "CHAPITRE II", "Section 1"],
                alineas=[Alinea(index=i, text=a) for i, a in enumerate(alineas)],
                source_file=source.filename,
            )
        )
    return Document(
        document_id=document_id,
        source=source,
        metadata=metadata,
        extraction=ExtractionResult(
            document_id=document_id,
            source_file=source.filename,
            method=ExtractionMethod.NATIVE,
            pages=pages,
        ),
        articles=articles,
    )


COURT = "Le contrat de travail est conclu librement entre les parties."
# Volontairement au-delà de chunking.max_chars (1500 par défaut).
LONG_ALINEA = "Cette disposition s'applique a tous les employeurs du secteur prive. " * 40


# ---------------------------------------------------------------------------
# Chunking (§20)
# ---------------------------------------------------------------------------


class TestSentenceSplitting:
    def test_splits_on_sentence_boundaries(self):
        parts = split_sentences("Le contrat est conclu. Il peut etre rompu. Ainsi soit-il.")
        assert len(parts) == 3

    def test_never_returns_empty_for_non_empty_input(self):
        assert split_sentences("un fragment sans ponctuation") == ["un fragment sans ponctuation"]

    def test_empty_input(self):
        assert split_sentences("   ") == []


class TestPacking:
    def test_units_are_never_split(self):
        units = ["a" * 30, "b" * 30, "c" * 30]
        blocks = pack_units(units, max_chars=70)
        assert all("a" * 30 in " ".join(blocks) for _ in [0])
        assert len(blocks) == 2

    def test_oversized_unit_is_kept_whole(self):
        """Une unité indivisible plus longue que la cible n'est pas coupée."""
        blocks = pack_units(["x" * 500], max_chars=100)
        assert blocks == ["x" * 500]

    def test_overlap_repeats_the_tail(self):
        units = [f"unite{i}" for i in range(10)]
        without = pack_units(units, max_chars=25, overlap_chars=0)
        with_overlap = pack_units(units, max_chars=25, overlap_chars=10)
        assert len(with_overlap) >= len(without)


class TestArticleChunking:
    def test_short_article_is_one_chunk(self, config):
        document = make_document(article_specs=[("1", COURT, [])])
        chunks = chunk_document(document, config)
        assert len(chunks) == 1
        assert chunks[0].text == COURT

    def test_article_is_the_priority_unit(self, config):
        """§20 : priorité Article → alinéas → paragraphes."""
        document = make_document(
            article_specs=[("1", COURT, []), ("2", COURT, []), ("3", COURT, [])]
        )
        chunks = chunk_document(document, config)
        assert len(chunks) == 3, "un article qui tient reste entier"

    def test_long_article_is_split_on_alineas(self, config):
        alineas = [f"Alinea numero {i} : " + "texte juridique complet. " * 8 for i in range(12)]
        document = make_document(article_specs=[("5", " ".join(alineas), alineas)])
        chunks = chunk_document(document, config)
        assert len(chunks) > 1
        # Aucun alinéa n'est coupé en deux : chacun se retrouve entier quelque part.
        joined = " ".join(chunk.text for chunk in chunks)
        for alinea in alineas:
            assert alinea.strip() in joined

    def test_single_huge_alinea_falls_back_to_sentences(self, config):
        document = make_document(article_specs=[("7", LONG_ALINEA, [LONG_ALINEA])])
        chunks = chunk_document(document, config)
        assert len(chunks) > 1
        # Le découpage a lieu sur des frontières de phrase.
        for chunk in chunks[:-1]:
            assert chunk.text.rstrip().endswith(".")

    def test_no_sentence_is_cut_in_half(self, config):
        text = " ".join(f"Phrase juridique numero {i} du present article." for i in range(60))
        document = make_document(article_specs=[("9", text, [text])])
        chunks = chunk_document(document, config)
        reassembled = " ".join(chunk.text for chunk in chunks)
        for i in range(60):
            assert f"Phrase juridique numero {i} du present article." in reassembled

    def test_chunk_carries_full_context(self, config):
        """§20 : document, article, chapitre, section, page, position."""
        document = make_document(article_specs=[("45", COURT, [])])
        chunk = chunk_document(document, config)[0]
        assert chunk.document_id == "loi_2026_001"
        assert chunk.article_id == "loi_2026_001_article_45"
        assert chunk.article_number == "45"
        assert chunk.title == "TITRE I"
        assert chunk.chapter == "CHAPITRE II"
        assert chunk.section == "Section 1"
        assert chunk.hierarchy_path == ["TITRE I", "CHAPITRE II", "Section 1"]
        assert chunk.page == 1
        assert chunk.position == 0

    def test_chunk_metadata_allows_citing_the_source(self, config):
        document = make_document(article_specs=[("45", COURT, [])])
        metadata = chunk_document(document, config)[0].metadata
        assert metadata["document_number"] == "2026-001"
        assert metadata["document_date"] == "2026-02-10"
        assert metadata["source_file"] == "loi_2026_001.pdf"
        assert metadata["page_start"] == 1

    def test_chunk_ids_are_unique(self, config):
        alineas = [f"Alinea {i} : " + "texte. " * 40 for i in range(8)]
        document = make_document(
            article_specs=[("1", " ".join(alineas), alineas), ("2", COURT, [])]
        )
        chunks = chunk_document(document, config)
        assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)

    def test_empty_article_produces_nothing(self, config):
        document = make_document(article_specs=[("1", "   ", [])])
        assert chunk_document(document, config) == []


class TestChunkingStrategies:
    def test_alinea_strategy(self, config):
        cfg = config.with_overrides({"chunking": {"strategy": "alinea"}})
        alineas = ["Premier alinea.", "Deuxieme alinea.", "Troisieme alinea."]
        document = make_document(article_specs=[("1", " ".join(alineas), alineas)])
        chunks = chunk_document(document, cfg)
        assert len(chunks) == 3
        assert chunks[1].text == "Deuxieme alinea."
        assert chunks[1].strategy == "alinea"

    def test_fixed_strategy_always_splits(self, config):
        cfg = config.with_overrides({"chunking": {"strategy": "fixed", "max_chars": 100}})
        long_text = "Disposition juridique detaillee. " * 20
        document = make_document(article_specs=[("1", long_text, [])])
        assert len(chunk_document(document, cfg)) > 1

    def test_unknown_strategy_falls_back(self, config, caplog):
        cfg = config.with_overrides({"chunking": {"strategy": "magique"}})
        document = make_document(article_specs=[("1", COURT, [])])
        with caplog.at_level("WARNING"):
            chunks = chunk_document(document, cfg)
        assert chunks and chunks[0].strategy == "article"
        assert any("inconnue" in record.message for record in caplog.records)

    def test_max_chars_comes_from_config(self, config):
        text = "Disposition juridique. " * 60
        document = make_document(article_specs=[("1", text, [])])
        large = config.with_overrides({"chunking": {"max_chars": 5000}})
        small = config.with_overrides({"chunking": {"max_chars": 200}})
        assert len(chunk_document(document, large)) < len(chunk_document(document, small))

    def test_absurd_overlap_is_corrected(self, config, caplog):
        cfg = config.with_overrides({"chunking": {"max_chars": 200, "overlap_chars": 500}})
        document = make_document(article_specs=[("1", "Texte. " * 100, [])])
        with caplog.at_level("WARNING"):
            chunks = chunk_document(document, cfg)
        assert chunks
        assert any("overlap" in record.message for record in caplog.records)


class TestFallbackWithoutArticles:
    def test_document_without_articles_still_produces_chunks(self, config, caplog):
        document = make_document(article_specs=[], page_texts=["Texte libre. " * 60])
        with caplog.at_level("WARNING"):
            chunks = chunk_document(document, config)
        assert chunks
        assert all(chunk.strategy == "page" for chunk in chunks)
        assert chunks[0].page == 1
        assert "aucun article" in chunks[0].metadata["note"]

    def test_stats(self, config):
        document = make_document(article_specs=[("1", COURT, []), ("2", COURT, [])])
        stats = chunking_stats(chunk_documents([document], config))
        assert stats["count"] == 2
        assert stats["by_strategy"]["article"] == 2

    def test_stats_on_empty_input(self):
        assert chunking_stats([])["count"] == 0


# ---------------------------------------------------------------------------
# Embeddings (§19) — optionnels
# ---------------------------------------------------------------------------


class TestEmbeddingsAreOptional:
    def test_disabled_by_default(self, config):
        """§4 : l'utilisation des embeddings doit être optionnelle."""
        assert config.get("embeddings.enabled") is False
        ready, problems = check_embeddings_ready(config)
        assert ready is False
        assert "désactivés" in problems[0]

    def test_missing_dependency_is_reported_clearly(self, config, monkeypatch):
        monkeypatch.setattr(embeddings_module, "embeddings_available", lambda: False)
        cfg = config.with_overrides({"embeddings": {"enabled": True}})
        ready, problems = check_embeddings_ready(cfg)
        assert ready is False
        assert "sentence-transformers" in problems[0]

    def test_model_is_not_loaded_at_import(self):
        """Importer le module ne doit jamais télécharger un modèle."""
        model = embeddings_module.EmbeddingModel()
        assert model._model is None

    def test_missing_dependency_raises_actionable_error(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("sentence_transformers"):
                raise ImportError("absent")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        with pytest.raises(EmbeddingsUnavailableError, match="sentence-transformers"):
            embeddings_module.EmbeddingModel().model

    def test_no_chunks_no_call(self, config):
        assert embed_chunks([], config) == []


class TestEmbeddingRecords:
    def test_records_keep_their_origin(self, config, monkeypatch):
        """§19 : les embeddings doivent conserver les métadonnées originales."""

        class FakeModel:
            name = "modele-de-test"

            def encode(self, texts, show_progress=False):
                return [[0.1, 0.2, 0.3] for _ in texts]

        document = make_document(article_specs=[("45", COURT, [])])
        chunks = chunk_document(document, config)
        records = embed_chunks(chunks, config, model=FakeModel())

        assert len(records) == 1
        record = records[0]
        assert record.document_id == "loi_2026_001"
        assert record.article_id == "loi_2026_001_article_45"
        assert record.article_number == "45"
        assert record.embedding_model == "modele-de-test"
        assert record.dimension == 3
        assert record.text == COURT

    def test_summary(self):
        records = [
            EmbeddingRecord(
                vector_id=f"v{i}", chunk_id=f"c{i}", document_id="doc",
                embedding_model="m", dimension=3, vector=[0.1, 0.2, 0.3],
            )
            for i in range(4)
        ]
        summary = embeddings_summary(records)
        assert summary == {"count": 4, "model": "m", "dimension": 3, "documents": 1}

    def test_empty_summary(self):
        assert embeddings_summary([])["count"] == 0


class TestSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_mismatched_dimensions(self):
        assert cosine_similarity([1, 0], [1, 0, 0]) == 0.0

    def test_zero_vector(self):
        assert cosine_similarity([0, 0], [1, 1]) == 0.0

    def test_brute_force_search_ranks_correctly(self):
        records = [
            EmbeddingRecord(
                vector_id="a", chunk_id="a", document_id="d", embedding_model="m",
                dimension=2, vector=[1.0, 0.0], text="proche",
            ),
            EmbeddingRecord(
                vector_id="b", chunk_id="b", document_id="d", embedding_model="m",
                dimension=2, vector=[0.0, 1.0], text="loin",
            ),
        ]
        hits = brute_force_search([1.0, 0.1], records, top_k=2)
        assert hits[0][0].chunk_id == "a"
        assert hits[0][1] > hits[1][1]


# ---------------------------------------------------------------------------
# Base vectorielle (§4) — optionnelle
# ---------------------------------------------------------------------------


class TestVectorStoreIsOptional:
    def test_disabled_by_default(self, config):
        ready, problems = check_vectorstore_ready(config)
        assert ready is False
        assert "désactivée" in problems[0]

    def test_qdrant_is_declared_out_of_scope(self, config):
        cfg = config.with_overrides(
            {"vectorstore": {"enabled": True, "backend": "qdrant"}}
        )
        ready, problems = check_vectorstore_ready(cfg)
        assert ready is False
        assert "ultérieure" in problems[0]

    def test_unknown_backend_is_rejected(self, config):
        cfg = config.with_overrides({"vectorstore": {"enabled": True, "backend": "magique"}})
        assert check_vectorstore_ready(cfg)[0] is False

    def test_missing_faiss_is_reported(self, config, monkeypatch):
        monkeypatch.setattr(vectorstore_module, "faiss_available", lambda: False)
        cfg = config.with_overrides({"vectorstore": {"enabled": True}})
        ready, problems = check_vectorstore_ready(cfg)
        assert ready is False
        assert "faiss" in problems[0]

    def test_indexing_degrades_without_failing(self, config, caplog):
        """L'absence d'index ne doit jamais faire échouer un corpus complet."""
        records = [
            EmbeddingRecord(
                vector_id="a", chunk_id="a", document_id="d",
                embedding_model="m", dimension=2, vector=[1.0, 0.0],
            )
        ]
        with caplog.at_level("WARNING"):
            assert index_embeddings(records, config) is None
        assert any("ignorée" in record.message for record in caplog.records)

    def test_build_store_raises_when_unavailable(self, config):
        with pytest.raises(VectorStoreUnavailableError):
            vectorstore_module.build_store(config, dimension=3)


@pytest.mark.requires_embeddings
class TestRealFaiss:
    """Exécutés seulement si FAISS est installé."""

    def test_roundtrip(self, tmp_path, config):
        if not vectorstore_module.faiss_available():
            pytest.skip("faiss n'est pas installé")

        from bldp.core.vectorstore import FaissStore

        records = [
            EmbeddingRecord(
                vector_id=f"v{i}", chunk_id=f"c{i}", document_id="doc",
                article_id=f"doc_article_{i}", article_number=str(i),
                embedding_model="m", dimension=3,
                vector=[float(i == j) for j in range(3)], text=f"texte {i}",
            )
            for i in range(3)
        ]
        store = FaissStore(tmp_path / "index.faiss", dimension=3)
        store.add(records)
        store.save()

        reloaded = FaissStore.load(tmp_path / "index.faiss")
        assert len(reloaded) == 3
        hits = reloaded.search([1.0, 0.0, 0.0], top_k=2)
        assert hits[0].chunk_id == "c0"
        assert hits[0].article_id == "doc_article_0", "un résultat doit citer sa source"

    def test_index_without_metadata_is_refused(self, tmp_path):
        if not vectorstore_module.faiss_available():
            pytest.skip("faiss n'est pas installé")

        from bldp.core.vectorstore import FaissStore, VectorStoreError

        records = [
            EmbeddingRecord(
                vector_id="v", chunk_id="c", document_id="d",
                embedding_model="m", dimension=3, vector=[1.0, 0.0, 0.0],
            )
        ]
        store = FaissStore(tmp_path / "index.faiss", dimension=3)
        store.add(records)
        store.save()
        (tmp_path / "index.faiss.meta.json").unlink()

        with pytest.raises(VectorStoreError, match="citer ses sources"):
            FaissStore.load(tmp_path / "index.faiss")
