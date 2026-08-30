"""Module 1 — importation des documents (§6 du cahier des charges).

Parcourt un dossier d'entrée, inventorie les fichiers pris en charge, calcule
leur empreinte et leur attribue un identifiant stable. Les originaux ne sont
**jamais** modifiés : lorsqu'une copie de travail est demandée, elle est écrite
dans ``data/raw/`` et l'original reste intact (§18).

Le classement manuel en sous-dossiers (``lois/``, ``codes/``...) est facultatif :
il est simplement retenu comme indice de catégorie, sans être obligatoire.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import SourceFile
from bldp.utils import hash_file, make_document_id, utc_now_iso

logger = get_logger("loader")

#: Sous-dossiers reconnus dans ``input/`` et catégorie associée.
KNOWN_CATEGORIES = (
    "lois",
    "codes",
    "decrets",
    "arretes",
    "jurisprudence",
    "autres",
)

#: Fichiers ignorés silencieusement lors du parcours.
_IGNORED_NAMES = {".gitkeep", ".DS_Store", "Thumbs.db"}


class LoaderError(RuntimeError):
    """Le dossier d'entrée est introuvable ou illisible."""


def discover_files(
    root: str | Path,
    extensions: Sequence[str] = (".pdf",),
    recursive: bool = True,
    follow_symlinks: bool = False,
) -> list[Path]:
    """Liste les fichiers candidats sous ``root``.

    Args:
        root: dossier d'entrée, ou fichier unique.
        extensions: extensions acceptées (comparaison insensible à la casse).
        recursive: descendre dans les sous-dossiers.
        follow_symlinks: suivre les liens symboliques (désactivé par défaut).

    Returns:
        Les chemins trouvés, triés pour une exécution reproductible.
    """
    base = Path(root)
    accepted = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}

    if base.is_file():
        return [base] if base.suffix.lower() in accepted else []
    if not base.exists():
        raise LoaderError(f"Dossier d'entrée introuvable : {base}")
    if not base.is_dir():
        raise LoaderError(f"Chemin d'entrée invalide : {base}")

    pattern = "**/*" if recursive else "*"
    found: list[Path] = []
    for candidate in base.glob(pattern):
        if candidate.name in _IGNORED_NAMES or candidate.name.startswith("~$"):
            continue
        if candidate.is_symlink() and not follow_symlinks:
            continue
        if candidate.is_file() and candidate.suffix.lower() in accepted:
            found.append(candidate)
    return sorted(found)


def detect_category(path: Path, root: Path) -> str:
    """Devine la catégorie d'après le sous-dossier d'origine.

    Le classement manuel n'étant pas obligatoire, tout dossier inconnu (ou
    l'absence de dossier) donne ``"autres"``.
    """
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return "autres"
    for part in relative.parts[:-1]:
        if part.lower() in KNOWN_CATEGORIES:
            return part.lower()
    return "autres"


def build_source_file(
    path: Path,
    root: Path,
    existing_ids: Iterable[str] = (),
) -> SourceFile:
    """Construit l'enregistrement d'inventaire d'un fichier.

    Le hachage est calculé sur le fichier tel quel, avant toute copie : c'est
    l'empreinte de référence pour la détection de doublons (§14).
    """
    stats = path.stat()
    digest = hash_file(path)
    document_id = make_document_id(path.name, digest, existing_ids)
    return SourceFile(
        document_id=document_id,
        source_path=str(path.resolve()),
        filename=path.name,
        extension=path.suffix.lower(),
        size_bytes=stats.st_size,
        file_hash=digest,
        ingested_at=utc_now_iso(),
        category=detect_category(path, root),
        modified_at=_iso_from_timestamp(stats.st_mtime),
    )


def copy_to_raw(source: SourceFile, raw_dir: str | Path) -> Path:
    """Copie l'original dans ``data/raw/`` sous un nom sans ambiguïté.

    La copie est nommée ``<document_id><extension>`` afin que le lien avec
    l'inventaire soit évident lors d'un audit manuel. Les métadonnées de
    fichier (dates) sont préservées ; l'original n'est jamais déplacé.
    """
    target_dir = Path(raw_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.document_id}{source.extension}"
    origin = Path(source.source_path)
    if target.resolve() == origin.resolve():
        return target
    shutil.copy2(origin, target)
    return target


def ingest(
    root: str | Path,
    config: Config,
    copy: bool | None = None,
) -> list[SourceFile]:
    """Inventorie un dossier d'entrée et renvoie les fichiers importés.

    Un fichier illisible n'interrompt pas l'importation : il est renvoyé avec
    son champ ``error`` renseigné, conformément au §26.
    """
    base = Path(root)
    extensions = config.get("ingest.extensions", [".pdf"])
    recursive = bool(config.get("ingest.recursive", True))
    follow = bool(config.get("ingest.follow_symlinks", False))
    should_copy = config.get("ingest.copy_to_raw", True) if copy is None else copy

    paths = discover_files(base, extensions, recursive, follow)
    logger.info("%d fichier(s) candidat(s) dans %s", len(paths), base)

    scan_root = base if base.is_dir() else base.parent
    sources: list[SourceFile] = []
    seen_ids: set[str] = set()

    for path in paths:
        try:
            source = build_source_file(path, scan_root, seen_ids)
        except OSError as exc:
            logger.error("Fichier illisible, ignoré : %s (%s)", path, exc)
            sources.append(_unreadable_source(path, str(exc)))
            continue

        seen_ids.add(source.document_id)
        if should_copy:
            try:
                source.raw_path = str(copy_to_raw(source, config.path("raw")))
            except OSError as exc:
                source.error = f"copie impossible vers data/raw : {exc}"
                logger.warning("Copie impossible pour %s : %s", path.name, exc)

        logger.info(
            "Document chargé : %s (%s, %d octets, categorie=%s)",
            source.document_id,
            source.filename,
            source.size_bytes,
            source.category,
        )
        sources.append(source)

    return sources


def iter_ingested(sources: Iterable[SourceFile]) -> Iterator[SourceFile]:
    """Itère uniquement sur les fichiers exploitables (sans erreur d'accès)."""
    for source in sources:
        if source.error is None or source.raw_path is not None:
            yield source


# ---------------------------------------------------------------------------
# Internes
# ---------------------------------------------------------------------------


def _iso_from_timestamp(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()


def _unreadable_source(path: Path, message: str) -> SourceFile:
    """Enregistrement minimal pour un fichier qu'on n'a pas pu ouvrir."""
    from bldp.utils import slugify

    return SourceFile(
        document_id=slugify(path.stem),
        source_path=str(path),
        filename=path.name,
        extension=path.suffix.lower(),
        size_bytes=0,
        file_hash="",
        ingested_at=utc_now_iso(),
        category="autres",
        error=message,
    )
