"""Journalisation du pipeline (§25 du cahier des charges).

Chaque traitement produit un log lisible en console et, si la configuration
l'autorise, un fichier persistant sous ``logs/``. Un log par exécution est
également écrit à côté du rapport de run pour permettre l'audit a posteriori.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOGGER_NAME = "bldp"

_CONSOLE_FORMAT = "[%(levelname)s] %(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class JsonLinesFormatter(logging.Formatter):
    """Formatte chaque enregistrement en une ligne JSON (logs machine)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("document_id", "page", "stage"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Renvoie le logger BLDP (ou un de ses enfants : ``bldp.parser``...)."""
    if name != LOGGER_NAME and not name.startswith(LOGGER_NAME + "."):
        name = f"{LOGGER_NAME}.{name}"
    return logging.getLogger(name)


def setup_logging(
    level: str = "INFO",
    to_file: bool = True,
    file_path: str | Path = "logs/bldp.log",
    json_lines: bool = False,
    run_log: Optional[str | Path] = None,
) -> logging.Logger:
    """Configure le logger racine du projet.

    Args:
        level: seuil (``DEBUG``/``INFO``/``WARNING``/``ERROR``).
        to_file: écrire aussi dans ``file_path``.
        file_path: journal cumulatif du projet.
        json_lines: format JSON Lines au lieu du texte lisible.
        run_log: journal supplémentaire propre à l'exécution en cours.

    Returns:
        Le logger ``bldp`` prêt à l'emploi.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Réinitialise : setup_logging peut être rappelé (tests, interface web).
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(
        JsonLinesFormatter() if json_lines else logging.Formatter(_CONSOLE_FORMAT)
    )
    logger.addHandler(console)

    file_formatter = JsonLinesFormatter() if json_lines else logging.Formatter(_FILE_FORMAT)

    for target in (file_path if to_file else None, run_log):
        if not target:
            continue
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(file_formatter)
        logger.addHandler(handler)

    return logger
