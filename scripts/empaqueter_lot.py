#!/usr/bin/env python3
"""Empaquette un lot traité, avec de quoi le vérifier et le reconstituer.

Le disque du serveur ne peut pas contenir les 60 Go de PDF du corpus complet.
Les sources doivent donc être effacées au fur et à mesure — ce qui n'est
acceptable qu'à une condition : **pouvoir prouver plus tard ce qui a été
traité, et le retrouver.**

C'est ce que produit ce script. L'archive contient trois choses :

``le corpus traité``
    exports JSONL, base SQLite, fiches par catégorie. C'est le produit.

``le manifeste de reconstitution``
    pour chaque document, son empreinte SHA-256 et l'URL de sa page
    d'origine. Un PDF effacé reste re-téléchargeable, et l'empreinte prouve
    que le fichier récupéré est identique **au bit près** à celui qui a été
    traité. Sans ce manifeste, effacer les sources serait irréversible.

``les métadonnées du catalogue``
    titre officiel, numéro, date de publication, catégorie, description, et
    la provenance champ par champ — jusqu'au sélecteur CSS dont chaque valeur
    a été extraite.

Les PDF eux-mêmes s'ajoutent avec ``--sources``. C'est le seul vrai filet :
le manifeste suppose que le portail conserve ses documents, ce qu'aucune
archive juridique ne devrait tenir pour acquis.

Usage ::

    python scripts/empaqueter_lot.py lot1 \\
        --exports /opt/bldp/lot1/data \\
        --lcf /var/lib/lcf/data \\
        --sortie /opt/bldp/archives \\
        [--sources]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bldp.core.crawl import LcfIndex, normalize_hash  # noqa: E402


#: Les PDF sont déjà compressés : les recompresser coûte du temps de calcul
#: pour un gain de quelques pour cent. On se contente de les regrouper.
COMPRESSION_SOURCES = zipfile.ZIP_STORED

#: Le texte, lui, se comprime très bien — un JSONL d'articles perd les trois
#: quarts de son poids.
COMPRESSION_TEXTE = zipfile.ZIP_DEFLATED


def empreinte(chemin: Path) -> str:
    """SHA-256 d'un fichier, lu par blocs pour ne pas le charger en mémoire."""
    digest = hashlib.sha256()
    with chemin.open("rb") as handle:
        for bloc in iter(lambda: handle.read(1 << 20), b""):
            digest.update(bloc)
    return digest.hexdigest()


def construire_manifeste(
    lcf_dir: Path, empreintes: set[str] | None
) -> tuple[list[dict], dict]:
    """Le manifeste de reconstitution, depuis le catalogue de collecte.

    Args:
        lcf_dir: dossier de données du collecteur.
        empreintes: empreintes de contenu à retenir ; ``None`` pour tout le
            catalogue.

    Returns:
        ``(fiches, résumé)``.
    """
    fiches: list[dict] = []
    par_source: dict[str, int] = {}

    with LcfIndex(lcf_dir) as index:
        for fiche in index.records():
            if empreintes is not None and normalize_hash(fiche.content_hash) not in empreintes:
                continue
            par_source[fiche.source_id] = par_source.get(fiche.source_id, 0) + 1
            fiches.append(
                {
                    "document_id": fiche.document_id,
                    "source_id": fiche.source_id,
                    # Les deux clefs de la reconstitution : où le reprendre,
                    # et comment prouver que c'est bien le même.
                    "url": fiche.url,
                    "content_hash": fiche.content_hash,
                    "byte_size": fiche.byte_size,
                    "fetched_at": fiche.fetched_at,
                    "titre": fiche.title,
                    "numero": fiche.number,
                    "categorie": fiche.category,
                    "description": fiche.description,
                    "publie_le": fiche.published_at,
                    "provenance": fiche.provenance,
                }
            )

    resume = {
        "documents": len(fiches),
        "par_source": par_source,
        "octets_sources": sum(f["byte_size"] for f in fiches),
    }
    return fiches, resume


def empreintes_du_lot(exports_dir: Path) -> set[str] | None:
    """Empreintes des documents réellement présents dans les sorties du lot.

    Deux choix ici. On se fie à la **base produite** plutôt qu'à une liste
    tenue à la main : ce qui a été traité est ce qui est dans la base, pas ce
    qu'on croyait y mettre. Et on joint sur l'**empreinte du contenu** plutôt
    que sur le nom du fichier, comme partout ailleurs dans la chaîne — un
    document renommé reste le même document.

    Returns:
        Les empreintes, ou ``None`` si la base est absente ou illisible : le
        manifeste couvre alors tout le catalogue, ce qui est plus large mais
        jamais faux.
    """
    import sqlite3

    base = exports_dir / "exports" / "legal_database.sqlite"
    if not base.exists():
        print(f"  base introuvable ({base}) — manifeste étendu au catalogue entier",
              file=sys.stderr)
        return None

    connexion = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    try:
        lignes = connexion.execute(
            "SELECT file_hash FROM documents WHERE file_hash IS NOT NULL"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"  base illisible ({exc}) — manifeste étendu au catalogue entier",
              file=sys.stderr)
        return None
    finally:
        connexion.close()

    return {h.strip().lower() for (h,) in lignes if h}


def ajouter_dossier(archive: zipfile.ZipFile, dossier: Path, prefixe: str) -> int:
    """Verse un dossier dans l'archive, en conservant son arborescence."""
    if not dossier.exists():
        return 0
    ajoutes = 0
    for fichier in sorted(dossier.rglob("*")):
        if fichier.is_file():
            archive.write(fichier, f"{prefixe}/{fichier.relative_to(dossier)}")
            ajoutes += 1
    return ajoutes


def main() -> int:
    # La console Windows parle cp1252 : une fleche ou un guillemet francais y
    # leve une exception, et le script meurt APRES avoir ecrit l'archive — en
    # laissant croire qu'il a echoue. On force donc la sortie en UTF-8.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


    parseur = argparse.ArgumentParser(
        description="Empaquette un lot traité avec son manifeste de reconstitution."
    )
    parseur.add_argument("lot", help="nom du lot, ex. « lot1 »")
    parseur.add_argument("--exports", required=True, type=Path,
                         help="dossier data/ du lot (exports, traites, processed)")
    parseur.add_argument("--lcf", required=True, type=Path,
                         help="dossier de données du collecteur")
    parseur.add_argument("--sortie", required=True, type=Path,
                         help="où déposer les archives")
    parseur.add_argument("--sources", action="store_true",
                         help="joindre aussi les PDF d'origine (archive séparée)")
    parseur.add_argument("--audit", action="store_true",
                         help="joindre les PDF OCRisés conservés pour audit")
    args = parseur.parse_args()

    args.sortie.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"Lot « {args.lot} »")
    ids = empreintes_du_lot(args.exports)
    print(f"  documents dans la base : {len(ids) if ids is not None else 'tous'}")

    fiches, resume = construire_manifeste(args.lcf, ids)
    print(f"  fiches de catalogue    : {resume['documents']}")
    if ids and resume["documents"] < len(ids):
        # Un document traité sans fiche ne serait pas reconstituable : il faut
        # le savoir avant d'effacer quoi que ce soit.
        manquants = len(ids) - resume["documents"]
        print(f"  ATTENTION : {manquants} document(s) traité(s) sans fiche de "
              "catalogue — ils ne seraient pas reconstituables.", file=sys.stderr)

    manifeste = {
        "lot": args.lot,
        "cree_le": horodatage,
        "outil": "bldp/scripts/empaqueter_lot.py",
        "resume": resume,
        "reconstitution": (
            "Chaque document porte son URL d'origine et l'empreinte SHA-256 du "
            "fichier traité. Pour reconstituer une source effacée : télécharger "
            "l'URL, calculer le SHA-256, et le comparer à content_hash. S'ils "
            "coïncident, le fichier est identique au bit près à celui qui a "
            "produit ce corpus."
        ),
        "documents": fiches,
    }

    # -- archive du corpus -------------------------------------------------
    chemin_corpus = args.sortie / f"{args.lot}-corpus-{horodatage}.zip"
    print(f"\n  → {chemin_corpus.name}")
    with zipfile.ZipFile(chemin_corpus, "w", COMPRESSION_TEXTE, allowZip64=True) as z:
        z.writestr(
            f"{args.lot}/manifeste.json",
            json.dumps(manifeste, ensure_ascii=False, indent=2),
        )
        n = ajouter_dossier(z, args.exports / "exports", f"{args.lot}/exports")
        print(f"     exports  : {n} fichier(s)")
        n = ajouter_dossier(z, args.exports / "traites", f"{args.lot}/traites")
        print(f"     fiches   : {n} fichier(s)")
        if args.audit:
            n = ajouter_dossier(z, args.exports / "processed", f"{args.lot}/audit")
            print(f"     audit    : {n} PDF OCRisé(s)")
    _annoncer(chemin_corpus)

    # -- archive des sources ------------------------------------------------
    if args.sources:
        chemin_sources = args.sortie / f"{args.lot}-sources-{horodatage}.zip"
        print(f"\n  → {chemin_sources.name}")
        with zipfile.ZipFile(
            chemin_sources, "w", COMPRESSION_SOURCES, allowZip64=True
        ) as z:
            z.writestr(
                f"{args.lot}/manifeste.json",
                json.dumps(manifeste, ensure_ascii=False, indent=2),
            )
            with LcfIndex(args.lcf) as index:
                joints = 0
                for fiche in index.records():
                    if ids is not None and normalize_hash(fiche.content_hash) not in ids:
                        continue
                    if fiche.content_path.exists():
                        z.write(
                            fiche.content_path,
                            f"{args.lot}/sources/{fiche.document_id}.pdf",
                        )
                        joints += 1
        print(f"     PDF      : {joints}")
        _annoncer(chemin_sources)

    print(
        "\n  Les archives portent leur propre empreinte ci-dessus : vérifiez-la "
        "après transfert, avant d'effacer quoi que ce soit du serveur."
    )
    return 0


def _annoncer(chemin: Path) -> None:
    taille = chemin.stat().st_size
    print(f"     poids    : {taille / 1024 / 1024:.0f} Mo")
    print(f"     sha256   : {empreinte(chemin)}")


if __name__ == "__main__":
    raise SystemExit(main())
