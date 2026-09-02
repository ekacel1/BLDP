"""Interface web minimale (§22 du cahier des charges).

Le cahier des charges est explicite : cette interface est « souhaitable mais non
prioritaire », et il ne faut « pas construire une interface complexe au début ».
Elle se limite donc strictement aux huit fonctions listées au §22 :

* téléverser un PDF ;
* lancer le traitement ;
* afficher la progression ;
* prévisualiser le texte ;
* prévisualiser les articles ;
* afficher les erreurs ;
* valider manuellement ;
* télécharger le résultat.

Contraintes de conception :

* **aucun appel externe** — pas de CDN, pas de police distante : le HTML et le
  CSS sont servis localement, conformément au §27 ;
* le serveur n'écoute que sur ``127.0.0.1`` par défaut ;
* le traitement s'exécute en arrière-plan afin que la page reste réactive, et
  l'état de chaque tâche est consultable.

FastAPI est une dépendance **optionnelle** : le pipeline et la CLI fonctionnent
entièrement sans elle.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from bldp import __version__
from bldp.config import Config, load_config
from bldp.logging_setup import get_logger
from bldp.utils import utc_now_iso

logger = get_logger("web")

# Les symboles FastAPI doivent être importés au **niveau du module**, et non
# dans `create_app` : avec `from __future__ import annotations`, les annotations
# des routes deviennent des chaînes que FastAPI résout dans les globales du
# module. Un `UploadFile` importé localement resterait introuvable.
try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # dépendance optionnelle : le pipeline s'en passe
    FASTAPI_AVAILABLE = False


class WebUnavailableError(RuntimeError):
    """FastAPI n'est pas installé."""


def web_available() -> bool:
    import importlib.util

    return FASTAPI_AVAILABLE and importlib.util.find_spec("uvicorn") is not None


# ---------------------------------------------------------------------------
# Suivi des tâches
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """État d'un traitement lancé depuis l'interface."""

    job_id: str
    filename: str
    status: str = "en_attente"      # en_attente | en_cours | termine | echoue
    stage: str = ""
    current: int = 0
    total: int = 0
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    document_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "current": self.current,
            "total": self.total,
            "progress": round(self.current / self.total, 2) if self.total else 0.0,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "document_ids": self.document_ids,
            "errors": self.errors,
            "summary": self.summary,
        }


class JobRegistry:
    """Registre en mémoire des traitements en cours.

    Volontairement non persistant : l'interface du MVP est un outil de travail
    local, pas un service. La vérité reste la base SQLite.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], filename=filename)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def create_app(config: Config | None = None) -> Any:
    """Construit l'application FastAPI.

    Raises:
        WebUnavailableError: FastAPI ou uvicorn absent.
    """
    if not FASTAPI_AVAILABLE:
        raise WebUnavailableError(
            "FastAPI est requis pour l'interface web. "
            'Installez-le avec : pip install -e ".[web]"'
        )

    config = config or load_config()
    config.ensure_directories()
    registry = JobRegistry()
    upload_dir = config.path("raw").parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Benin Legal Data Pipeline",
        version=__version__,
        description="Interface locale de traitement et de validation du corpus juridique.",
    )
    app.state.config = config
    app.state.registry = registry
    app.state.upload_dir = upload_dir

    # -- Pages -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return render_index(config)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "jurisdiction": config.get("project.jurisdiction"),
            "external_calls_allowed": config.get("privacy.allow_external_calls"),
        }

    # -- Téléversement et traitement (§22.1-3) ------------------------------

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> JSONResponse:
        """Reçoit un PDF et lance son traitement en arrière-plan."""
        name = Path(file.filename or "document.pdf").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

        job = registry.create(name)
        # Chaque dépôt a son propre dossier : deux fichiers homonymes ne se
        # marchent pas dessus, et le nom d'origine est préservé — sans quoi
        # l'identifiant de la tâche se retrouverait dans le `document_id` du
        # corpus, ce qui n'a aucun sens juridique.
        destination = app.state.upload_dir / job.job_id / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
        finally:
            await file.close()

        thread = threading.Thread(
            target=_run_job, args=(job, destination, config), daemon=True
        )
        thread.start()
        return JSONResponse({"job_id": job.job_id, "filename": name})

    @app.get("/api/jobs")
    def list_jobs() -> dict:
        return {"jobs": [job.to_dict() for job in registry.list()]}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        """Progression du traitement (§22.3)."""
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Traitement inconnu.")
        return job.to_dict()

    # -- Tableau de bord ----------------------------------------------------

    @app.get("/api/stats")
    def stats() -> dict:
        """Chiffres du corpus, pour le tableau de bord.

        Tout vient de la base : la page ne calcule rien, elle affiche.
        """
        with _database(config) as database:
            if database is None:
                return {"disponible": False}
            counts = {
                cle: database.connection.execute(requete).fetchone()[0] or 0
                for cle, requete in {
                    "documents": "SELECT COUNT(*) FROM documents",
                    "pages": "SELECT COALESCE(SUM(page_count), 0) FROM documents",
                    "articles": "SELECT COUNT(*) FROM articles",
                    "alineas": "SELECT COUNT(*) FROM alineas",
                    "a_verifier": (
                        "SELECT COUNT(*) FROM documents WHERE validation = 'a_verifier'"
                    ),
                }.items()
            }
            par_categorie = [
                {"categorie": row["category"] or "autres", "documents": row["n"]}
                for row in database.connection.execute(
                    "SELECT category, COUNT(*) AS n FROM documents "
                    "GROUP BY category ORDER BY n DESC"
                )
            ]
        etapes: list[dict] = []
        try:
            from bldp.core.tracking import STAGE_BADGES, Stage, TrackingRegistry

            with TrackingRegistry(config.path("database")) as suivi:
                compte = suivi.counts_by_stage()
            for stage in Stage:
                n = compte.get(stage.value, 0)
                if n:
                    marker, label = STAGE_BADGES[stage]
                    etapes.append(
                        {"etape": stage.value, "badge": marker, "libelle": label, "tickets": n}
                    )
        except Exception:  # noqa: BLE001 — le suivi ne bloque jamais la page
            pass
        return {
            "disponible": True,
            "compteurs": counts,
            "par_categorie": par_categorie,
            "par_etape": etapes,
        }

    # -- Relecture assistée : état, jamais déclenchement ---------------------

    @app.get("/api/relecture")
    def relecture_etat(etape: str = "a_verifier", limit: int = 20) -> dict:
        """État de la relecture assistée, et ce qu'un lot représenterait.

        Cette route **ne déclenche rien**. Un bouton de navigateur est le pire
        endroit pour décider d'envoyer des documents hors de la machine : un
        clic n'est pas un consentement éclairé. La relecture se lance depuis
        le terminal, où la commande annonce le lot et son coût avant tout
        appel (``python -m bldp relire --oui``).
        """
        from bldp.core.review import check_ready, plan_review
        from bldp.core.storage.sqlite_store import LegalDatabase, load_document
        from bldp.core.tracking import Stage, TrackingRegistry

        pret, obstacles = check_ready(config)
        etat = {
            "disponible": pret,
            "obstacles": obstacles,
            "modele": str(config.get("ai_review.model", "")),
            "peut_valider": bool(config.get("ai_review.can_validate", False)),
            "commande": f"python -m bldp relire --etape {etape} --oui",
            "collation_sur_image": bool(
                config.get("ai_review.send_page_images", True)
            ),
            "images": 0,
            "documents": [],
        }

        database_path = config.path("database")
        if not database_path.exists():
            return etat
        try:
            stage = Stage(etape)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Étape inconnue : {etape}")

        with TrackingRegistry(database_path) as suivi:
            identifiants = [
                t.document_id for t in suivi.list_tickets(stage, None, limit)
            ]
        with LegalDatabase(database_path, create=False) as database:
            documents = [
                document
                for document in (
                    load_document(database, identifiant)
                    for identifiant in identifiants
                )
                if document is not None
            ]

        plan = plan_review(documents, config)
        etat["documents"] = [d.to_dict() for d in plan.documents]
        etat["collation_sur_image"] = plan.collation
        etat["images"] = sum(d.images for d in plan.eligible)
        etat["cout_estime_usd"] = round(plan.estimated_usd, 2)
        etat["cout_plafond_usd"] = round(plan.ceiling_usd, 2)
        return etat

    # -- Suivi : tickets et décisions ---------------------------------------

    @app.get("/api/suivi")
    def suivi_liste(etape: str = "") -> dict:
        """Tickets du registre de suivi, filtrables par étape."""
        from bldp.core.tracking import Stage, TrackingRegistry

        database_path = config.path("database")
        if not database_path.exists():
            return {"tickets": []}
        try:
            stage = Stage(etape) if etape else None
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Étape inconnue : {etape}")
        with TrackingRegistry(database_path) as suivi:
            return {"tickets": [t.to_dict() for t in suivi.list_tickets(stage)]}

    @app.get("/api/suivi/{reference}")
    def suivi_detail(reference: str) -> dict:
        """Fiche d'un ticket et son journal complet."""
        from bldp.core.tracking import TrackingRegistry, allowed_transitions

        with TrackingRegistry(config.path("database")) as suivi:
            ticket = suivi.resolve(reference)
            if ticket is None:
                raise HTTPException(status_code=404, detail="Ticket inconnu.")
            fiche = ticket.to_dict()
            fiche["journal"] = [e.to_dict() for e in suivi.history(ticket.ticket_id)]
            fiche["etapes_possibles"] = sorted(
                s.value for s in allowed_transitions(ticket.stage)
            )
            return fiche

    @app.post("/api/suivi/{reference}/avancer")
    def suivi_avancer(
        reference: str,
        etape: str = Form(...),
        par: str = Form(...),
        motif: str = Form(""),
    ) -> dict:
        """Change l'étape d'un ticket — les règles du registre s'appliquent.

        C'est le registre qui décide : transitions contrôlées, et jamais de
        validation sans un acteur humain nommé (§16). L'interface ne fait que
        transmettre, et affiche le refus tel quel.
        """
        from bldp.core.tracking import Stage, TrackingRegistry
        from bldp.core.tracking.registry import TrackingError

        try:
            stage = Stage(etape)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Étape inconnue : {etape}")
        with TrackingRegistry(config.path("database")) as suivi:
            ticket = suivi.resolve(reference)
            if ticket is None:
                raise HTTPException(status_code=404, detail="Ticket inconnu.")
            try:
                mis = suivi.advance(ticket.ticket_id, stage, par.strip(), motif.strip())
            except TrackingError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return mis.to_dict()

    @app.post("/api/suivi/{reference}/assigner")
    def suivi_assigner(
        reference: str, personne: str = Form(""), par: str = Form("interface")
    ) -> dict:
        from bldp.core.tracking import TrackingRegistry
        from bldp.core.tracking.registry import TrackingError

        with TrackingRegistry(config.path("database")) as suivi:
            ticket = suivi.resolve(reference)
            if ticket is None:
                raise HTTPException(status_code=404, detail="Ticket inconnu.")
            try:
                mis = suivi.assign(ticket.ticket_id, personne.strip() or None, par)
            except TrackingError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return mis.to_dict()

    # -- Consultation (§22.4-6) ---------------------------------------------

    @app.get("/api/documents")
    def list_documents() -> dict:
        with _database(config) as database:
            if database is None:
                return {"documents": []}
            return {
                "documents": [
                    {
                        "document_id": row["document_id"],
                        "title": row["title"],
                        "type": row["type"],
                        "number": row["number"],
                        "date": row["date"],
                        "pages": row["page_count"],
                        "category": row["category"] or "autres",
                        "validation": row["validation"],
                    }
                    for row in database.list_documents()
                ]
            }

    @app.get("/api/documents/{document_id}")
    def document_detail(document_id: str) -> dict:
        """Texte, articles et erreurs d'un document (§22.4-6)."""
        with _database(config) as database:
            if database is None:
                raise HTTPException(status_code=404, detail="Aucun corpus enregistré.")
            row = database.get_document_row(document_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Document inconnu.")

            quality = database.get_quality(document_id)
            issues = database.connection.execute(
                "SELECT code, severity, message, page FROM quality_issues "
                "WHERE document_id = ? ORDER BY severity DESC",
                (document_id,),
            ).fetchall()

            return {
                "document": dict(row),
                "pages": [
                    {
                        "page": page["page"],
                        "text": page["text"],
                        "raw_text": page["raw_text"],
                        "char_count": page["char_count"],
                    }
                    for page in database.get_pages(document_id)
                ],
                "articles": [
                    {
                        "article_id": article["article_id"],
                        "article_number": article["article_number"],
                        "text": article["text"],
                        "page_start": article["page_start"],
                        "title": article["title"],
                        "chapter": article["chapter"],
                        "section": article["section"],
                    }
                    for article in database.get_articles(document_id)
                ],
                "quality": dict(quality) if quality else None,
                "issues": [dict(issue) for issue in issues],
            }

    @app.get("/api/articles/{article_id}/trace")
    def trace(article_id: str) -> dict:
        """Chaîne article → page → fichier source (§33)."""
        with _database(config) as database:
            if database is None:
                raise HTTPException(status_code=404, detail="Aucun corpus enregistré.")
            result = database.trace_article(article_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Article inconnu.")
            return result

    # -- Validation manuelle (§22.7) ----------------------------------------

    @app.post("/api/documents/{document_id}/validation")
    def set_validation(document_id: str, status: str = Form(...), note: str = Form("")) -> dict:
        from bldp.models import ValidationStatus

        try:
            decision = ValidationStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Statut invalide : attendu valide, a_verifier, rejete ou en_attente.",
            )

        with _database(config) as database:
            if database is None or database.get_document_row(document_id) is None:
                raise HTTPException(status_code=404, detail="Document inconnu.")
            database.set_validation(document_id, decision, note)
        return {"document_id": document_id, "validation": decision.value, "note": note}

    # -- Téléchargement (§22.8) ---------------------------------------------

    @app.get("/api/exports")
    def list_exports() -> dict:
        folder = config.path("exports")
        if not folder.exists():
            return {"files": []}
        return {
            "files": [
                {"name": path.name, "size_bytes": path.stat().st_size}
                for path in sorted(folder.iterdir())
                if path.is_file()
            ]
        }

    @app.get("/api/exports/{name}")
    def download(name: str):
        # On refuse tout chemin composé : seul un fichier du dossier d'export
        # peut être servi.
        if Path(name).name != name:
            raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
        target = config.path("exports") / name
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Fichier introuvable.")
        return FileResponse(target, filename=name)

    logger.info("Interface web prête (version %s)", __version__)
    return app


# ---------------------------------------------------------------------------
# Exécution d'une tâche
# ---------------------------------------------------------------------------


def _run_job(job: Job, path: Path, config: Config) -> None:
    """Traite un fichier téléversé, en tenant le registre à jour."""
    from bldp.pipeline import run_pipeline

    job.status = "en_cours"
    job.total = 1

    def progress(rank: int, total: int, document_id: str, stage: str) -> None:
        job.current, job.total, job.stage = rank, total, stage

    try:
        result = run_pipeline(path, config, progress=progress)
        job.document_ids = [d.document_id for d in result.documents]
        job.errors = [error for d in result.documents for error in d.errors]
        report = result.report
        job.summary = {
            "total": report.total if report else 0,
            "succeeded": report.succeeded if report else 0,
            "review_required": report.review_required if report else 0,
            "failed": report.failed if report else 0,
        }
        job.status = "termine"
        job.stage = "terminé"
    except Exception as exc:  # noqa: BLE001 — une tâche ne doit jamais tuer le serveur
        logger.error("Traitement %s en échec : %s", job.job_id, exc, exc_info=True)
        job.status = "echoue"
        job.errors.append(str(exc))
    finally:
        job.finished_at = utc_now_iso()


class _NullDatabase:
    """Substitut silencieux lorsque la base n'existe pas encore."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc_info: Any) -> None:
        return None


def _database(config: Config):
    """Ouvre la base si elle existe, sinon un contexte renvoyant ``None``."""
    from bldp.core.storage.sqlite_store import LegalDatabase

    path = config.path("database")
    if not path.exists():
        return _NullDatabase()
    return LegalDatabase(path, create=False)


# ---------------------------------------------------------------------------
# Page HTML
# ---------------------------------------------------------------------------


def render_index(config: Config) -> str:
    """Page unique de l'interface, sans aucune ressource externe (§27)."""
    template = Path(__file__).parent / "templates" / "index.html"
    html = template.read_text(encoding="utf-8")
    return (
        html.replace("{{VERSION}}", __version__)
        .replace("{{JURIDICTION}}", str(config.get("project.jurisdiction", "generic")))
    )


def serve(
    config: Config | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> None:
    """Démarre le serveur local.

    L'écoute est limitée à la boucle locale par défaut : le corpus peut
    contenir des documents non encore publiés, il n'a pas à être exposé au
    réseau (§27).
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise WebUnavailableError(
            'uvicorn est requis pour servir l\'interface. pip install -e ".[web]"'
        ) from exc

    if host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "Le serveur écoute sur %s : le corpus devient accessible depuis le "
            "réseau. Assurez-vous que c'est bien voulu.",
            host,
        )

    app = create_app(config)
    logger.info("Interface disponible sur http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, reload=reload, log_level="info")
