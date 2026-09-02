"""Lecture des catalogues de collecte, et confrontation aux documents.

Un collecteur qui aspire un portail juridique produit deux choses : les
fichiers, et une fiche par fichier. Ce sous-module lit la seconde et la met en
regard de ce que la chaîne d'extraction a lu dans le premier.

Le principe ne change pas : **le document fait foi, le catalogue est un
témoin**. Les trous se comblent, les accords renforcent la confiance, les
divergences se signalent — elles ne s'écrasent jamais.
"""

from bldp.core.crawl.lcf import (
    AGREEMENT_CONFIDENCE,
    CATALOGUE_CONFIDENCE,
    CATEGORY_TO_TYPE,
    CrawlIndexError,
    CrawlRecord,
    Discrepancy,
    LcfIndex,
    load_catalogue,
    normalize_hash,
    reconcile,
)

__all__ = [
    "AGREEMENT_CONFIDENCE",
    "CATALOGUE_CONFIDENCE",
    "CATEGORY_TO_TYPE",
    "CrawlIndexError",
    "CrawlRecord",
    "Discrepancy",
    "LcfIndex",
    "load_catalogue",
    "normalize_hash",
    "reconcile",
]
