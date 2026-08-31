"""Module 4 — OCR des documents scannés (§7 du cahier des charges).

Deux moteurs sont pris en charge, avec repli automatique de l'un sur l'autre :

``ocrmypdf``
    Produit un **nouveau PDF** contenant une couche texte par-dessus l'image
    d'origine. C'est la voie privilégiée : le PDF OCRisé est conservé dans
    ``data/processed/ocr/`` et reste consultable côte à côte avec l'original,
    ce qui rend la vérification humaine possible (§16).

``tesseract``
    OCR page par page à partir d'un rendu bitmap. Utilisé lorsque ``ocrmypdf``
    n'est pas installé, ou pour un OCR **ciblé** sur quelques pages faibles
    d'un document par ailleurs lisible (route ``hybrid``).

Aucun document n'est envoyé à un service externe : les deux moteurs tournent
localement (§27). Si aucun moteur n'est disponible, le pipeline ne s'arrête
pas : il signale l'impossibilité et poursuit avec le texte natif disponible
(§26), plutôt que de produire un corpus silencieusement amputé.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import ExtractionMethod, ExtractionResult, Page
from bldp.core.extraction.pymupdf_extractor import (
    ExtractionError,
    extract_document,
    open_pdf,
)

logger = get_logger("extraction.ocr")


class OcrUnavailableError(RuntimeError):
    """Aucun moteur OCR utilisable sur cette machine."""


class OcrError(RuntimeError):
    """L'OCR a été tenté mais a échoué sur ce document."""


# ---------------------------------------------------------------------------
# Disponibilité des moteurs
# ---------------------------------------------------------------------------


def ocrmypdf_available() -> bool:
    """Vrai si le binaire ``ocrmypdf`` est utilisable."""
    return shutil.which("ocrmypdf") is not None


def tesseract_available() -> bool:
    """Vrai si le binaire ``tesseract`` est utilisable."""
    return shutil.which("tesseract") is not None


def available_engines() -> list[str]:
    """Moteurs OCR effectivement installés, par ordre de préférence."""
    engines = []
    if ocrmypdf_available():
        engines.append("ocrmypdf")
    if tesseract_available():
        engines.append("tesseract")
    return engines


def tesseract_languages() -> list[str]:
    """Langues installées pour Tesseract (``[]`` si indéterminable)."""
    if not tesseract_available():
        return []
    try:
        completed = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    lines = (completed.stdout or "").splitlines()
    return [line.strip() for line in lines[1:] if line.strip()]


def check_ocr_ready(config: Config) -> tuple[bool, list[str]]:
    """Vérifie que l'OCR configuré est réellement exécutable.

    Returns:
        ``(prêt, problèmes)`` — la liste décrit ce qu'il manque, en clair.
    """
    problems: list[str] = []
    if not config.get("ocr.enabled", True):
        return False, ["l'OCR est désactivé dans la configuration (ocr.enabled=false)"]

    engines = available_engines()
    if not engines:
        problems.append(
            "aucun moteur OCR installé — installez tesseract et/ou ocrmypdf "
            "(https://ocrmypdf.readthedocs.io/en/latest/installation.html)"
        )
        return False, problems

    # OCRmyPDF n'est qu'un **pilote** : il rastérise le PDF et délègue la
    # reconnaissance à Tesseract. Le déclarer suffisant alors que Tesseract
    # manque revient à annoncer un OCR opérationnel qui échouera à la première
    # page — la dégradation silencieuse que le projet cherche à éviter.
    if not tesseract_available():
        problems.append(
            "tesseract est introuvable dans le PATH. OCRmyPDF s'appuie sur lui "
            "et ne peut fonctionner seul : installez Tesseract, puis rouvrez "
            "un terminal."
        )
        return False, problems

    language = str(config.get("ocr.language", "fra"))
    installed = tesseract_languages()
    if not installed:
        problems.append(
            "impossible de lister les langues de Tesseract "
            "(`tesseract --list-langs` muet). Vérifiez TESSDATA_PREFIX : il doit "
            "pointer vers le dossier contenant les fichiers .traineddata."
        )
        return False, problems

    missing = [lang for lang in language.split("+") if lang not in installed]
    if missing:
        problems.append(
            f"langue(s) Tesseract absente(s) : {', '.join(missing)} "
            f"(installées : {', '.join(installed)})"
        )
    return not problems, problems


# ---------------------------------------------------------------------------
# OCRmyPDF
# ---------------------------------------------------------------------------


def run_ocrmypdf(
    source: str | Path,
    target: str | Path,
    language: str = "fra",
    dpi: int = 300,
    skip_text: bool = True,
    timeout: int = 1800,
    force: bool = False,
    jobs: int = 0,
) -> Path:
    """Produit une version OCRisée du PDF avec ``ocrmypdf``.

    Args:
        source: PDF d'origine (jamais modifié).
        target: PDF de sortie, avec couche texte.
        language: langues Tesseract (``fra``, ``fra+eng``...).
        dpi: résolution de rastérisation des pages sans texte.
        skip_text: ne pas ré-OCRiser les pages qui ont déjà du texte.
        timeout: délai maximal en secondes.
        force: ré-OCRiser même les pages contenant du texte (exclusif de
            ``skip_text``).
        jobs: fils internes d'OCRmyPDF ; ``0`` laisse l'outil décider. À fixer
            à 1 lorsque plusieurs documents sont traités en parallèle, sinon
            les deux niveaux de parallélisme se multiplient et saturent la
            machine au lieu de l'accélérer.

    Raises:
        OcrUnavailableError: binaire absent.
        OcrError: échec de l'exécution ou délai dépassé.
    """
    if not ocrmypdf_available():
        raise OcrUnavailableError(
            "ocrmypdf est introuvable. Installez-le (pip install ocrmypdf) "
            "ainsi que Tesseract et Ghostscript."
        )

    source_path, target_path = Path(source), Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = [
        "ocrmypdf",
        "--language", language,
        "--image-dpi", str(dpi),
        "--output-type", "pdf",
        "--quiet",
    ]
    # `--force-ocr` et `--skip-text` sont mutuellement exclusifs.
    if force:
        command.append("--force-ocr")
    elif skip_text:
        command.append("--skip-text")
    if jobs > 0:
        command += ["--jobs", str(jobs)]
    command += [str(source_path), str(target_path)]

    logger.info("OCR (ocrmypdf) sur %s → %s", source_path.name, target_path.name)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"OCR interrompu après {timeout}s sur {source_path.name}") from exc
    except OSError as exc:
        raise OcrError(f"Exécution d'ocrmypdf impossible : {exc}") from exc

    if completed.returncode != 0 or not target_path.exists():
        message = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = message[-1] if message else f"code de retour {completed.returncode}"
        raise OcrError(f"ocrmypdf a échoué sur {source_path.name} : {detail}")

    return target_path


# ---------------------------------------------------------------------------
# Tesseract page par page
# ---------------------------------------------------------------------------


def run_tesseract_on_page(
    document: Any,
    page_index: int,
    language: str = "fra",
    dpi: int = 300,
    timeout: int = 300,
) -> tuple[str, Optional[float]]:
    """OCRise une page rendue en bitmap et renvoie ``(texte, confiance)``.

    La confiance est celle rapportée par Tesseract (moyenne des mots), ramenée
    entre 0 et 1 ; ``None`` si elle n'a pas pu être mesurée.
    """
    if not tesseract_available():
        raise OcrUnavailableError("tesseract est introuvable dans le PATH.")

    import tempfile

    page = document.load_page(page_index)
    pixmap = page.get_pixmap(dpi=dpi)

    with tempfile.TemporaryDirectory(prefix="bldp_ocr_") as workdir:
        image_path = Path(workdir) / f"page_{page_index + 1}.png"
        pixmap.save(image_path)
        text = _tesseract_text(image_path, language, timeout)
        confidence = _tesseract_confidence(image_path, language, timeout)
    return text, confidence


def _tesseract_text(image_path: Path, language: str, timeout: int) -> str:
    command = ["tesseract", str(image_path), "stdout", "-l", language]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"Tesseract interrompu après {timeout}s ({image_path.name})") from exc
    except OSError as exc:
        raise OcrError(f"Exécution de tesseract impossible : {exc}") from exc

    if completed.returncode != 0:
        raise OcrError(f"tesseract a échoué : {_tesseract_failure(completed)}")
    return completed.stdout or ""


def _tesseract_failure(completed: Any) -> str:
    """Explique un échec de Tesseract de façon exploitable.

    « erreur inconnue » n'aide personne. Tesseract écrit parfois sur la sortie
    standard plutôt que sur l'erreur, et se tait complètement lorsqu'il tombe
    à court de mémoire — situation qu'on rencontre en traitant plusieurs gros
    documents de front. Le code de retour devient alors la seule information
    disponible, et il vaut mieux le dire que prétendre ne rien savoir.
    """
    for flux in (completed.stderr, completed.stdout):
        lignes = [l.strip() for l in (flux or "").splitlines() if l.strip()]
        if lignes:
            return lignes[-1]

    code = completed.returncode
    if code in (-9, 137, 3221225725):  # tué par le système, ou pile épuisée
        return (
            f"arrêté par le système (code {code}) — mémoire probablement "
            "insuffisante. Réduisez --workers ou ocr.dpi."
        )
    return (
        f"code de retour {code}, sans message. Vérifiez TESSDATA_PREFIX et "
        "relancez ce document seul (python -m bldp parse <fichier>)."
    )


def _tesseract_confidence(image_path: Path, language: str, timeout: int) -> Optional[float]:
    """Confiance moyenne rapportée par Tesseract, via la sortie TSV."""
    command = ["tesseract", str(image_path), "stdout", "-l", language, "tsv"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None

    scores: list[float] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) < 12:
            continue
        try:
            score = float(columns[10])
        except ValueError:
            continue
        # -1 = ligne de structure sans mot reconnu.
        if score >= 0 and columns[11].strip():
            scores.append(score)
    if not scores:
        return None
    return round(sum(scores) / len(scores) / 100.0, 4)


def ocr_pages_with_tesseract(
    path: str | Path,
    document_id: str,
    pages: Optional[Sequence[int]] = None,
    language: str = "fra",
    dpi: int = 300,
    timeout: int = 300,
    source_file: str | None = None,
) -> ExtractionResult:
    """OCRise tout ou partie d'un PDF page par page.

    Args:
        pages: numéros de page 1-based à traiter ; ``None`` = toutes.

    Une page qui échoue est renvoyée vide et signalée : elle n'interrompt pas
    le traitement des autres (§26).
    """
    started = time.perf_counter()
    target = Path(path)
    label = source_file or target.name

    result = ExtractionResult(
        document_id=document_id,
        source_file=label,
        method=ExtractionMethod.OCR,
    )

    document = open_pdf(target)
    try:
        wanted = set(pages) if pages else set(range(1, document.page_count + 1))
        for index in range(document.page_count):
            page_number = index + 1
            if page_number not in wanted:
                continue
            try:
                text, confidence = run_tesseract_on_page(
                    document, index, language=language, dpi=dpi, timeout=timeout
                )
                warnings: list[str] = []
            except (OcrError, OcrUnavailableError) as exc:
                logger.warning("OCR échoué page %d de %s : %s", page_number, document_id, exc)
                text, confidence, warnings = "", None, [f"ocr_echoue: {exc}"]
                result.errors.append(f"page {page_number} : {exc}")

            if not text.strip() and not warnings:
                warnings.append("ocr_sans_texte")

            result.pages.append(
                Page(
                    document_id=document_id,
                    page=page_number,
                    text=text,
                    source_file=label,
                    raw_text=text,
                    method=ExtractionMethod.OCR,
                    ocr_confidence=confidence,
                    warnings=warnings,
                )
            )
    finally:
        document.close()

    result.duration_seconds = round(time.perf_counter() - started, 3)
    logger.info(
        "%s : OCR de %d page(s) en %.1fs (%d caractères)",
        document_id,
        len(result.pages),
        result.duration_seconds,
        result.total_chars,
    )
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def ocr_document(
    path: str | Path,
    document_id: str,
    config: Config,
    output_dir: str | Path | None = None,
    pages: Optional[Sequence[int]] = None,
    source_file: str | None = None,
) -> ExtractionResult:
    """OCRise un document complet en choisissant le meilleur moteur disponible.

    Stratégie :

    1. ``ocrmypdf`` sur le document entier, puis extraction native du PDF
       produit — c'est ce qui donne le meilleur texte et laisse une trace
       auditable (le PDF OCRisé est conservé) ;
    2. à défaut, Tesseract page par page ;
    3. si aucun moteur n'est disponible, :class:`OcrUnavailableError` est levée
       — l'appelant décide alors de se replier sur le texte natif.

    Args:
        pages: restreint l'OCR à ces pages (route ``hybrid``). Force l'emploi
            de Tesseract, ocrmypdf travaillant sur le document entier.
    """
    language = str(config.get("ocr.language", "fra"))
    dpi = int(config.get("ocr.dpi", 300))
    skip_text = bool(config.get("ocr.skip_text", True))
    timeout = int(config.get("ocr.timeout_seconds", 1800))
    keep_pdf = bool(config.get("ocr.keep_sidecar_pdf", True))
    jobs = int(config.get("ocr.jobs", 0))
    preferred = str(config.get("ocr.engine", "ocrmypdf"))

    engines = available_engines()
    if not engines:
        raise OcrUnavailableError(
            "Aucun moteur OCR disponible (ni ocrmypdf ni tesseract). "
            "Le document ne peut pas être OCRisé sur cette machine."
        )

    # OCR ciblé : ocrmypdf ne sait pas travailler sur un sous-ensemble de pages.
    if pages:
        if "tesseract" not in engines:
            raise OcrUnavailableError(
                "L'OCR ciblé sur certaines pages requiert Tesseract, absent de cette machine."
            )
        return ocr_pages_with_tesseract(
            path, document_id, pages, language, dpi, min(timeout, 600), source_file
        )

    order = [preferred] + [e for e in engines if e != preferred]
    failures: list[str] = []

    for engine in order:
        if engine not in engines:
            continue
        try:
            if engine == "ocrmypdf":
                return _ocr_via_ocrmypdf(
                    path, document_id, config, output_dir, language, dpi,
                    skip_text, timeout, keep_pdf, source_file, jobs,
                )
            return ocr_pages_with_tesseract(
                path, document_id, None, language, dpi, min(timeout, 600), source_file
            )
        except (OcrError, ExtractionError) as exc:
            failures.append(f"{engine}: {exc}")
            logger.warning("Moteur %s en échec sur %s : %s", engine, document_id, exc)

    raise OcrError(
        f"Tous les moteurs OCR ont échoué sur {document_id} — " + " | ".join(failures)
    )


def _ocr_via_ocrmypdf(
    path: str | Path,
    document_id: str,
    config: Config,
    output_dir: str | Path | None,
    language: str,
    dpi: int,
    skip_text: bool,
    timeout: int,
    keep_pdf: bool,
    source_file: str | None,
    jobs: int = 0,
) -> ExtractionResult:
    """OCRmyPDF puis relecture native du PDF enrichi."""
    import tempfile

    destination = Path(output_dir) if output_dir else config.path("processed") / "ocr"
    destination.mkdir(parents=True, exist_ok=True)

    if keep_pdf:
        ocr_pdf = destination / f"{document_id}_ocr.pdf"
        produced = run_ocrmypdf(path, ocr_pdf, language, dpi, skip_text, timeout, jobs=jobs)
        result = extract_document(
            produced, document_id, source_file=source_file or Path(path).name,
            sort_blocks=bool(config.get("extraction.sort_blocks", True)),
        )
        result.ocr_pdf_path = str(produced)
    else:
        with tempfile.TemporaryDirectory(prefix="bldp_ocr_") as workdir:
            produced = run_ocrmypdf(
                path, Path(workdir) / "ocr.pdf", language, dpi, skip_text, timeout,
                jobs=jobs,
            )
            result = extract_document(
                produced, document_id, source_file=source_file or Path(path).name
            )

    result.method = ExtractionMethod.OCR
    for page in result.pages:
        page.method = ExtractionMethod.OCR
    result.warnings.append(f"texte produit par OCR (ocrmypdf, langue={language})")
    return result


def extract_with_route(
    path: str | Path,
    document_id: str,
    route: str,
    config: Config,
    ocr_pages: Optional[Sequence[int]] = None,
    source_file: str | None = None,
) -> ExtractionResult:
    """Applique l'itinéraire décidé par le module 2 (``native``/``ocr``/``hybrid``).

    En cas d'indisponibilité de l'OCR, on se replie sur l'extraction native et
    on **signale** la dégradation dans les avertissements plutôt que d'échouer
    ou de produire un texte silencieusement incomplet.
    """
    label = source_file or Path(path).name

    sort_blocks = bool(config.get("extraction.sort_blocks", True))

    if route == "native":
        return extract_document(
            path, document_id, source_file=label, sort_blocks=sort_blocks
        )

    if route == "ocr":
        try:
            return ocr_document(path, document_id, config, source_file=label)
        except (OcrUnavailableError, OcrError) as exc:
            logger.error("OCR impossible pour %s : %s", document_id, exc)
            fallback = extract_document(
                path, document_id, source_file=label, sort_blocks=sort_blocks
            )
            fallback.warnings.append(
                f"OCR requis mais indisponible ({exc}) — texte natif partiel, "
                "vérification humaine nécessaire"
            )
            fallback.errors.append(f"ocr_indisponible: {exc}")
            return fallback

    if route == "hybrid":
        return _extract_hybrid(path, document_id, config, ocr_pages or [], label)

    raise ValueError(f"Itinéraire d'extraction inconnu : {route!r}")


def _extract_hybrid(
    path: str | Path,
    document_id: str,
    config: Config,
    ocr_pages: Sequence[int],
    label: str,
) -> ExtractionResult:
    """Texte natif partout, OCR uniquement sur les pages pauvres en texte."""
    result = extract_document(
        path, document_id, source_file=label,
        sort_blocks=bool(config.get("extraction.sort_blocks", True)),
    )
    if not ocr_pages:
        return result

    try:
        ocr_result = ocr_document(
            path, document_id, config, pages=list(ocr_pages), source_file=label
        )
    except (OcrUnavailableError, OcrError) as exc:
        logger.warning("OCR ciblé impossible pour %s : %s", document_id, exc)
        result.warnings.append(
            f"{len(ocr_pages)} page(s) pauvres en texte n'ont pas pu être OCRisées ({exc})"
        )
        return result

    replaced = 0
    by_number = {page.page: page for page in ocr_result.pages}
    for index, page in enumerate(result.pages):
        candidate = by_number.get(page.page)
        # On ne remplace que si l'OCR apporte réellement plus de texte : en cas
        # de doute, l'original natif est conservé (§9).
        if candidate and len(candidate.text.strip()) > len(page.text.strip()):
            candidate.warnings.append("page_ocrisee_en_remplacement_du_natif")
            result.pages[index] = candidate
            replaced += 1

    result.method = ExtractionMethod.MIXED if replaced else ExtractionMethod.NATIVE
    result.warnings.append(
        f"extraction mixte : {replaced}/{len(ocr_pages)} page(s) améliorée(s) par OCR"
    )
    result.duration_seconds = round(result.duration_seconds + ocr_result.duration_seconds, 3)
    return result
