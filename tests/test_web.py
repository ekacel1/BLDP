"""Tests de l'interface web minimale (§22).

L'interface étant explicitement « non prioritaire », les tests se concentrent
sur ce qui doit être juste : les huit fonctions listées au §22, l'absence
d'appel externe (§27), et le refus des chemins de fichiers composés.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bldp.core.storage.sqlite_store import LegalDatabase
from bldp.pipeline import run_pipeline
from bldp.web.app import Job, JobRegistry, render_index, web_available

fastapi = pytest.importorskip("fastapi", reason="FastAPI requis pour l'interface web")
pytest.importorskip("httpx", reason="httpx requis par le TestClient de FastAPI")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def populated(tmp_path, make_text_pdf, config):
    """Un corpus déjà traité, pour les routes de consultation."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    source = make_text_pdf(
        "loi_2026_001.pdf",
        [
            "REPUBLIQUE DU BENIN\n"
            "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
            "portant organisation du travail\n\n"
            "TITRE PREMIER\nDISPOSITIONS GENERALES\n\n"
            "Article 1er : La presente loi fixe les regles applicables aux "
            "relations de travail.\n\n"
            "Article 2 : Est considere comme travailleur toute personne physique.\n"
        ],
    )
    Path(source).replace(folder / "loi_2026_001.pdf")
    run_pipeline(folder, config)
    return config


@pytest.fixture
def client(config):
    from bldp.web.app import create_app

    return TestClient(create_app(config))


def wait_for_job(client, job_id: str, timeout: float = 30.0) -> dict:
    """Attend la fin d'un traitement lancé en arrière-plan.

    Sans cette attente, le thread de traitement écrit encore dans ``tmp_path``
    quand pytest le supprime : la suite devient intermittente, avec des erreurs
    de teardown sans rapport avec le test lui-même.
    """
    import time

    deadline = time.monotonic() + timeout
    payload = client.get(f"/api/jobs/{job_id}").json()
    while payload["status"] in {"en_attente", "en_cours"} and time.monotonic() < deadline:
        time.sleep(0.05)
        payload = client.get(f"/api/jobs/{job_id}").json()
    return payload


@pytest.fixture
def client_with_data(populated):
    from bldp.web.app import create_app

    return TestClient(create_app(populated))


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class TestApp:
    def test_web_dependencies_are_detected(self):
        assert web_available() is True

    def test_health(self, client):
        payload = client.get("/api/health").json()
        assert payload["status"] == "ok"
        assert payload["external_calls_allowed"] is False

    def test_home_page_renders(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Benin Legal Data Pipeline" in response.text

    def test_page_makes_no_external_request(self, config):
        """§27 : aucune ressource distante, pas même une police ou un CDN."""
        # Les commentaires sont retirés : ils *décrivent* la règle (« pas de
        # CDN ») sans la violer, et fausseraient la recherche.
        html = re.sub(r"<!--.*?-->", "", render_index(config), flags=re.DOTALL)
        assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', html), (
            "l'interface ne doit charger aucune ressource externe"
        )
        assert not re.search(r"@import\s+url\(\s*['\"]?https?://", html)
        assert not re.search(r"""fetch\(\s*['"]https?://""", html)

    def test_version_is_injected(self, config):
        from bldp import __version__

        assert __version__ in render_index(config)


# ---------------------------------------------------------------------------
# §22.1-3 — dépôt, traitement, progression
# ---------------------------------------------------------------------------


class TestUploadAndProgress:
    def test_upload_starts_a_job(self, client, text_pdf):
        with text_pdf.open("rb") as handle:
            response = client.post(
                "/api/upload",
                files={"file": ("loi.pdf", handle, "application/pdf")},
            )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert job_id

        payload = wait_for_job(client, job_id)
        assert payload["status"] == "termine"
        assert payload["document_ids"] == ["loi"]
        assert payload["summary"]["total"] == 1

    def test_non_pdf_is_rejected(self, client):
        response = client.post(
            "/api/upload", files={"file": ("note.txt", b"contenu", "text/plain")}
        )
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]

    def test_job_progress_is_observable(self, client, text_pdf):
        with text_pdf.open("rb") as handle:
            job_id = client.post(
                "/api/upload", files={"file": ("loi.pdf", handle, "application/pdf")}
            ).json()["job_id"]

        payload = client.get(f"/api/jobs/{job_id}").json()
        assert payload["job_id"] == job_id
        assert payload["status"] in {"en_attente", "en_cours", "termine", "echoue"}
        assert 0.0 <= payload["progress"] <= 1.0

        final = wait_for_job(client, job_id)
        assert final["status"] in {"termine", "echoue"}
        assert final["finished_at"]

    def test_unknown_job(self, client):
        assert client.get("/api/jobs/inexistant").status_code == 404

    def test_job_registry_is_thread_safe_enough(self):
        registry = JobRegistry()
        jobs = [registry.create(f"doc{i}.pdf") for i in range(5)]
        assert len({job.job_id for job in jobs}) == 5
        assert len(registry.list()) == 5
        assert registry.get(jobs[0].job_id) is jobs[0]

    def test_job_serialisation(self):
        job = Job(job_id="abc", filename="loi.pdf", current=2, total=4)
        payload = job.to_dict()
        assert payload["progress"] == 0.5
        assert payload["status"] == "en_attente"

    def test_job_progress_without_total(self):
        assert Job(job_id="a", filename="f.pdf").to_dict()["progress"] == 0.0


# ---------------------------------------------------------------------------
# §22.4-6 — aperçu du texte, des articles, des erreurs
# ---------------------------------------------------------------------------


class TestConsultation:
    def test_documents_are_listed(self, client_with_data):
        documents = client_with_data.get("/api/documents").json()["documents"]
        assert any(d["document_id"] == "loi_2026_001" for d in documents)

    def test_empty_corpus_is_not_an_error(self, client):
        assert client.get("/api/documents").json() == {"documents": []}

    def test_detail_returns_text_articles_and_issues(self, client_with_data):
        payload = client_with_data.get("/api/documents/loi_2026_001").json()
        assert payload["pages"], "§22.4 : aperçu du texte"
        assert payload["articles"], "§22.5 : aperçu des articles"
        assert "issues" in payload, "§22.6 : affichage des erreurs"
        assert payload["quality"] is not None

    def test_detail_exposes_raw_and_cleaned_text(self, client_with_data):
        """Les deux couches doivent être comparables dans l'interface (§16)."""
        page = client_with_data.get("/api/documents/loi_2026_001").json()["pages"][0]
        assert page["text"] is not None
        assert page["raw_text"] is not None

    def test_articles_carry_their_hierarchy(self, client_with_data):
        articles = client_with_data.get("/api/documents/loi_2026_001").json()["articles"]
        assert articles[0]["article_number"] == "1er"
        assert articles[0]["page_start"] == 1
        assert articles[0]["title"] == "TITRE PREMIER"

    def test_unknown_document(self, client_with_data):
        assert client_with_data.get("/api/documents/inexistant").status_code == 404

    def test_trace_endpoint(self, client_with_data):
        articles = client_with_data.get("/api/documents/loi_2026_001").json()["articles"]
        trace = client_with_data.get(f"/api/articles/{articles[0]['article_id']}/trace").json()
        assert trace["page"]["page"] == 1
        assert trace["source_path"].endswith(".pdf")

    def test_trace_unknown_article(self, client_with_data):
        assert client_with_data.get("/api/articles/inexistant/trace").status_code == 404


# ---------------------------------------------------------------------------
# §22.7 — validation manuelle
# ---------------------------------------------------------------------------


class TestValidation:
    def test_three_decisions_are_accepted(self, client_with_data, populated):
        for decision in ("valide", "a_verifier", "rejete"):
            response = client_with_data.post(
                "/api/documents/loi_2026_001/validation",
                data={"status": decision, "note": "revue manuelle"},
            )
            assert response.status_code == 200
            assert response.json()["validation"] == decision

        with LegalDatabase(populated.path("database"), create=False) as database:
            row = database.get_document_row("loi_2026_001")
        assert row["validation"] == "rejete"
        assert row["validation_note"] == "revue manuelle"

    def test_invalid_status_is_rejected(self, client_with_data):
        response = client_with_data.post(
            "/api/documents/loi_2026_001/validation", data={"status": "parfait"}
        )
        assert response.status_code == 400

    def test_validation_on_unknown_document(self, client_with_data):
        response = client_with_data.post(
            "/api/documents/inexistant/validation", data={"status": "valide"}
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# §22.8 — téléchargement
# ---------------------------------------------------------------------------


class TestDownloads:
    def test_exports_are_listed(self, client_with_data):
        names = {f["name"] for f in client_with_data.get("/api/exports").json()["files"]}
        assert "documents.jsonl" in names
        assert "articles.jsonl" in names

    def test_download_works(self, client_with_data):
        response = client_with_data.get("/api/exports/articles.jsonl")
        assert response.status_code == 200
        assert response.content

    def test_missing_file(self, client_with_data):
        assert client_with_data.get("/api/exports/absent.jsonl").status_code == 404

    @pytest.mark.parametrize(
        "name", ["../config/default.yaml", "..%2Fdefault.yaml", "sous/dossier.json"]
    )
    def test_path_traversal_is_refused(self, client_with_data, name):
        """Seul un fichier du dossier d'export peut être servi."""
        response = client_with_data.get(f"/api/exports/{name}")
        assert response.status_code in {400, 404}
        assert b"jurisdiction:" not in response.content

    def test_empty_exports_directory(self, client):
        assert client.get("/api/exports").json()["files"] == []
