"""Juridiction « generic » : le socle, sans aucune spécificité nationale.

Sert de repli lorsque ``project.jurisdiction`` désigne un pays non encore
couvert, et de modèle pour en ajouter un nouveau.
"""

from __future__ import annotations

from bldp.core.parser.rules import RuleSet
from bldp.jurisdictions.registry import JurisdictionProfile


def build() -> JurisdictionProfile:
    """Profil neutre : seules les règles génériques s'appliquent."""
    return JurisdictionProfile(
        name="generic",
        display_name="Juridiction générique (français juridique)",
        language="fr",
        ruleset=RuleSet(name="generic"),
    )
