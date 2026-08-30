"""Chargement et fusion de la configuration.

La configuration vit dans des fichiers YAML ; aucun paramètre métier ne doit
être codé en dur dans le reste du code (§23 du cahier des charges).

Ordre de précédence (du plus faible au plus fort) :

1. ``config/default.yaml`` (livré avec le projet) ;
2. ``config/local.yaml`` (surcharges de la machine, non versionné) ;
3. fichier passé explicitement via ``--config`` ;
4. surcharges ponctuelles ``--set section.cle=valeur``.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

#: Racine du dépôt (BLDP/), déduite de l'emplacement de ce fichier.
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "local.yaml"


class ConfigError(RuntimeError):
    """Configuration absente, illisible ou incohérente."""


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict:
    """Fusionne récursivement ``override`` dans ``base`` (sans muter les entrées)."""
    result = dict(copy.deepcopy(base))
    for key, value in override.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _coerce_scalar(raw: str) -> Any:
    """Convertit une valeur de ligne de commande vers un type Python.

    On réutilise le parseur YAML pour obtenir la même sémantique que les
    fichiers de configuration (``true``, ``3``, ``0.9``, ``[a, b]``, ``null``).
    """
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


class Config:
    """Vue en lecture seule sur l'arbre de configuration.

    Accès par chemin pointé, avec valeur par défaut explicite ::

        cfg.get("ocr.language", "fra")
        cfg["parser"]["detect_articles"]
    """

    def __init__(self, data: Mapping[str, Any], sources: Iterable[Path] = ()) -> None:
        self._data = copy.deepcopy(dict(data))
        self.sources = [Path(s) for s in sources]

    # -- accès ---------------------------------------------------------------
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return copy.deepcopy(node) if isinstance(node, (dict, list)) else node

    def require(self, path: str) -> Any:
        sentinel = object()
        value = self.get(path, sentinel)
        if value is sentinel:
            raise ConfigError(f"Clé de configuration manquante : {path}")
        return value

    def section(self, name: str) -> dict:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, path: str) -> bool:
        sentinel = object()
        return self.get(path, sentinel) is not sentinel

    def as_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def with_overrides(self, overrides: Mapping[str, Any]) -> "Config":
        return Config(_deep_merge(self._data, overrides), self.sources)

    # -- chemins -------------------------------------------------------------
    def path(self, key: str) -> Path:
        """Résout ``paths.<key>`` en chemin absolu ancré sur la racine projet."""
        raw = self.require(f"paths.{key}")
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else (self.root / candidate)

    @property
    def root(self) -> Path:
        return Path(self._data.get("_root", PROJECT_ROOT))

    def ensure_directories(self) -> None:
        """Crée les répertoires de travail s'ils n'existent pas encore."""
        for key in ("raw", "processed", "validated", "embeddings", "exports", "logs"):
            self.path(key).mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:  # pragma: no cover - confort de debug
        names = ", ".join(str(s.name) for s in self.sources) or "defaults"
        return f"<Config from {names}>"


def load_config(
    path: str | os.PathLike[str] | None = None,
    overrides: Iterable[str] = (),
    root: str | os.PathLike[str] | None = None,
) -> Config:
    """Construit la configuration effective.

    Args:
        path: fichier YAML supplémentaire (option ``--config``).
        overrides: surcharges ``"section.cle=valeur"`` (option ``--set``).
        root: racine projet à utiliser pour résoudre les chemins relatifs.
    """
    if not DEFAULT_CONFIG_PATH.exists():
        raise ConfigError(f"Configuration par défaut introuvable : {DEFAULT_CONFIG_PATH}")

    sources: list[Path] = [DEFAULT_CONFIG_PATH]
    data = _read_yaml(DEFAULT_CONFIG_PATH)

    if LOCAL_CONFIG_PATH.exists():
        data = _deep_merge(data, _read_yaml(LOCAL_CONFIG_PATH))
        sources.append(LOCAL_CONFIG_PATH)

    if path is not None:
        explicit = Path(path)
        if not explicit.exists():
            raise ConfigError(f"Fichier de configuration introuvable : {explicit}")
        data = _deep_merge(data, _read_yaml(explicit))
        sources.append(explicit)

    for override in overrides:
        if "=" not in override:
            raise ConfigError(f"Surcharge invalide (attendu cle=valeur) : {override!r}")
        key, _, raw_value = override.partition("=")
        data = _deep_merge(data, _nest(key.strip(), _coerce_scalar(raw_value.strip())))

    # Racine de travail. L'argument explicite prime ; à défaut on honore une
    # surcharge `--set _root=...` — l'écraser silencieusement ferait écrire le
    # pipeline dans le dépôt alors que l'appelant a demandé un autre dossier.
    if root is not None:
        data["_root"] = str(Path(root).resolve())
    elif data.get("_root"):
        data["_root"] = str(Path(str(data["_root"])).resolve())
    else:
        data["_root"] = str(PROJECT_ROOT)
    return Config(data, sources)


def _nest(dotted_key: str, value: Any) -> dict:
    """``"a.b.c", 1`` -> ``{"a": {"b": {"c": 1}}}``."""
    parts = dotted_key.split(".")
    node: Any = value
    for part in reversed(parts):
        node = {part: node}
    return node


def _read_yaml(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            content = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(content, dict):
        raise ConfigError(f"La racine de {path} doit être un dictionnaire.")
    return content
