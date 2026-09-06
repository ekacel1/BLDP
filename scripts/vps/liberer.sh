#!/usr/bin/env bash
# N'efface les sources d'un lot que si le telephone a prouve les avoir recues.
#
# La preuve est un recu depose par « bldp-sync.sh » dans /opt/bldp/recu/. Il
# atteste que l'archive a la bonne empreinte, que tous les fichiers sources
# sont arrives et qu'aucun n'est tronque. Sans recu, on n'efface rien.
#
# Ce script ne devine jamais. Il compte, il compare, et il refuse au moindre
# ecart — parce que les PDF sources n'existent nulle part ailleurs une fois
# partis d'ici.

set -uo pipefail

RECUS=/opt/bldp/recu
APPLIQUER=0
[ "${1:-}" = "--appliquer" ] && APPLIQUER=1

if [ "$APPLIQUER" -eq 0 ]; then
    echo "MODE CONSTAT — rien ne sera efface."
    echo "Pour effacer reellement : $0 --appliquer"
    echo
fi

libere=0
for chemin in /opt/bldp/lot*/input/decrets; do
    [ -d "$chemin" ] || continue
    lot=$(echo "$chemin" | cut -d/ -f4)
    recu="$RECUS/$lot.ok"
    ici=$(ls -1 "$chemin"/*.pdf 2>/dev/null | wc -l)
    taille=$(du -sh "$chemin" 2>/dev/null | cut -f1)

    printf '%-8s %6s fichier(s), %6s  ' "$lot" "$ici" "$taille"

    if [ ! -f "$recu" ]; then
        echo "AUCUN RECU — on garde"
        continue
    fi

    recus=$(awk -F': *' '/^sources/ {print $2}' "$recu" | grep -oE '^[0-9]+')
    if [ "${recus:-0}" -ne "$ici" ]; then
        echo "RECU INCOHERENT (${recus:-?} recus contre $ici ici) — on garde"
        continue
    fi

    # L'archive doit etre encore la, et porter l'empreinte du recu : le recu
    # atteste d'une archive precise, pas de n'importe laquelle.
    nom=$(awk -F': *' '/^archive/ {print $2}' "$recu")
    attendue=$(awk -F': *' '/^empreinte/ {print $2}' "$recu")
    if [ ! -f "/opt/bldp/archives/$nom" ]; then
        echo "ARCHIVE ABSENTE ($nom) — on garde"
        continue
    fi
    obtenue=$(sha256sum "/opt/bldp/archives/$nom" | cut -d' ' -f1)
    if [ "$obtenue" != "$attendue" ]; then
        echo "EMPREINTE DIVERGENTE — on garde"
        continue
    fi

    if [ "$APPLIQUER" -eq 1 ]; then
        rm -rf "$chemin"
        echo "EFFACE ($taille rendus)"
        libere=$((libere + 1))
    else
        echo "effacable ($taille)"
    fi
done

echo
df -h / | tail -1
[ "$APPLIQUER" -eq 1 ] && echo "$libere lot(s) libere(s)."
exit 0
