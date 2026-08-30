"""Registre des juridictions (§29 du cahier des charges).

Le cœur du pipeline (``bldp/core/``) ne connaît aucune particularité nationale.
Tout ce qui est propre à un pays — formes d'en-têtes, autorités émettrices,
formules de promulgation, types de documents — vit dans un module
``bldp/jurisdictions/<pays>/rules.py`` exposant une fonction ``build()``.

Ajouter le Togo ou le Sénégal ne demande donc aucune modification du cœur :
il suffit de créer le paquet correspondant et de renseigner
``project.jurisdiction`` dans la configuration.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional, Pattern

from bldp.core.parser.rules import RuleSet, generic_ruleset
from bldp.logging_setup import get_logger

logger = get_logger("jurisdictions")

#: Paquet racine où sont cherchées les juridictions.
JURISDICTIONS_PACKAGE = "bldp.jurisdictions"


@dataclass
class JurisdictionProfile:
    """Tout ce qui distingue une juridiction du socle générique.

    Attributes:
        name: identifiant, ex. ``"benin"``.
        display_name: libellé lisible, ex. ``"République du Bénin"``.
        language: langue principale des textes.
        ruleset: règles de parsing, déjà fusionnées avec le socle générique.
        document_type_patterns: motifs reconnaissant le type d'un document.
        authority_patterns: motifs reconnaissant l'autorité émettrice.
        number_patterns: motifs de numéros officiels (« 2026-001 »).
        official_sources: sources officielles connues, pour la métadonnée
            ``source``.
    """

    name: str
    display_name: str
    language: str = "fr"
    ruleset: RuleSet = field(default_factory=generic_ruleset)
    document_type_patterns: dict[str, list[Pattern[str]]] = field(default_factory=dict)
    authority_patterns: dict[str, list[Pattern[str]]] = field(default_factory=dict)
    number_patterns: list[Pattern[str]] = field(default_factory=list)
    date_patterns: list[Pattern[str]] = field(default_factory=list)
    official_sources: dict[str, str] = field(default_factory=dict)
    status_patterns: dict[str, list[Pattern[str]]] = field(default_factory=dict)
    relation_patterns: dict[str, list[Pattern[str]]] = field(default_factory=dict)


class JurisdictionError(LookupError):
    """La juridiction demandée n'existe pas ou est mal formée."""


def available_jurisdictions() -> list[str]:
    """Liste les juridictions installées (sous-paquets exposant ``rules``)."""
    try:
        package = importlib.import_module(JURISDICTIONS_PACKAGE)
    except ImportError:  # pragma: no cover - le paquet fait partie du projet
        return []

    names: list[str] = []
    for module in pkgutil.iter_modules(package.__path__):
        if not module.ispkg:
            continue
        try:
            importlib.import_module(f"{JURISDICTIONS_PACKAGE}.{module.name}.rules")
        except ImportError:
            continue
        names.append(module.name)
    return sorted(names)


@lru_cache(maxsize=None)
def get_jurisdiction(name: str) -> JurisdictionProfile:
    """Charge le profil d'une juridiction.

    Le profil renvoyé a déjà ses règles fusionnées avec le socle générique :
    une juridiction n'a jamais à redéclarer « Chapitre » ou « Article ».

    Raises:
        JurisdictionError: juridiction inconnue ou module invalide.
    """
    key = (name or "generic").strip().lower()
    module_name = f"{JURISDICTIONS_PACKAGE}.{key}.rules"

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        known = ", ".join(available_jurisdictions()) or "aucune"
        raise JurisdictionError(
            f"Juridiction inconnue : {name!r}. Juridictions disponibles : {known}."
        ) from exc

    builder = getattr(module, "build", None)
    if builder is None:
        raise JurisdictionError(
            f"Le module {module_name} doit exposer une fonction build() -> JurisdictionProfile."
        )

    profile = builder()
    if not isinstance(profile, JurisdictionProfile):
        raise JurisdictionError(
            f"{module_name}.build() doit renvoyer un JurisdictionProfile, "
            f"pas {type(profile).__name__}."
        )

    # Fusion systématique avec le socle générique.
    profile.ruleset = generic_ruleset().extend(profile.ruleset)
    logger.debug("Juridiction chargée : %s (%s)", profile.name, profile.display_name)
    return profile


def get_ruleset(config) -> RuleSet:
    """Jeu de règles correspondant à ``project.jurisdiction``.

    En cas de juridiction inconnue, on retombe sur le socle générique avec un
    avertissement : mieux vaut un parsing générique qu'un pipeline bloqué.
    """
    name = config.get("project.jurisdiction", "generic")
    try:
        return get_jurisdiction(name).ruleset
    except JurisdictionError as exc:
        logger.warning("%s Repli sur les règles génériques.", exc)
        return generic_ruleset()


def get_profile(config) -> Optional[JurisdictionProfile]:
    """Profil complet correspondant à la configuration, ou ``None``."""
    try:
        return get_jurisdiction(config.get("project.jurisdiction", "generic"))
    except JurisdictionError as exc:
        logger.warning("%s", exc)
        return None
