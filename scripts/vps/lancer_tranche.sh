#!/usr/bin/env bash
# Execute un cycle complet — collecte, traitement, archive — depuis le VPS.
#
# Usage :
#     ./lancer_tranche.sh              # sous tmux, detachable
#     ./lancer_tranche.sh --ici        # au premier plan, pour voir defiler
#
# Le cycle dure une a deux heures. Sous tmux il survit a la fermeture de votre
# terminal : c'est tout l'interet de le lancer d'ici plutot que d'un portable.

set -uo pipefail

RACINE=/opt/bldp-exploitation
CLI=/opt/colab-cli/bin/colab
CLE=/root/.ssh/colab_vm
DEPOT=/opt/bldp
CARNET=notebooks/collecte_traitement_sgg.ipynb
SESSION="bldp-$(date -u +%Y%m%dT%H%M%SZ)"
JOURNAL="$RACINE/journaux/$SESSION.log"

if [ "${1:-}" != "--ici" ]; then
    command -v tmux >/dev/null || { echo "tmux absent : utilisez --ici"; exit 1; }
    mkdir -p "$RACINE/journaux"
    tmux new-session -d -s "$SESSION" "$0 --ici 2>&1 | tee '$JOURNAL'"
    echo "Cycle lance dans tmux, session « $SESSION »."
    echo
    echo "  suivre      : tmux attach -t $SESSION      (Ctrl-b puis d pour detacher)"
    echo "  journal     : tail -f $JOURNAL"
    echo "  interrompre : tmux kill-session -t $SESSION"
    echo
    echo "ATTENTION : interrompre ne rend PAS la VM. Faites ensuite"
    echo "    $CLI --auth=oauth2 stop -s $SESSION"
    exit 0
fi

mkdir -p "$RACINE/journaux"

echoetape() { printf '\n=== %s — %s ===\n' "$(date -u +%H:%M:%SZ)" "$1"; }

# Rien ne rend une VM automatiquement avant 24 h. Sortir sans la rendre, meme
# sur une erreur ou une interruption, consommerait du quota pour rien.
rendre_la_vm() {
    echoetape "restitution de la VM"
    $CLI --auth=oauth2 stop -s "$SESSION" 2>&1 || true
    $CLI --auth=oauth2 sessions 2>&1 || true
}
trap rendre_la_vm EXIT INT TERM

echoetape "depot a jour"
git -C "$DEPOT" fetch --quiet origin && git -C "$DEPOT" pull --quiet --ff-only origin main
git -C "$DEPOT" log --oneline -1

echoetape "tranche demandee"
# La tranche est lue dans le carnet lui-meme : une seule source de verite.
/opt/colab-cli/bin/python - "$DEPOT/$CARNET" <<'PY'
import json, re, sys
nb = json.load(open(sys.argv[1], encoding="utf-8"))
for cellule in nb["cells"]:
    if cellule["cell_type"] != "code":
        continue
    source = "".join(cellule["source"])
    if "PAGE_DEBUT" in source and "NOM_LOT" in source:
        for cle in ("PAGE_DEBUT", "PAGE_FIN", "NOM_LOT"):
            trouve = re.search(rf"^{cle}\s*=\s*(\S+)", source, re.M)
            print(f"  {cle:<11} = {trouve.group(1) if trouve else '?'}")
        break
PY

echoetape "place disponible ici"
df -h / | tail -1

echoetape "allocation de la VM"
$CLI --auth=oauth2 new -s "$SESSION" || exit 1

echoetape "depot de la cle"
# Sans cette cle, la VM ne peut ni expedier les sources ni deposer l'archive.
$CLI --auth=oauth2 upload -s "$SESSION" "$CLE" /content/colab_bldp || exit 1

echoetape "execution du carnet"
# --timeout 36000 n'est pas facultatif : par defaut le client abandonne quand
# une cellule reste muette trop longtemps, ce que fait l'installation pendant
# plusieurs minutes.
cd "$DEPOT"
$CLI --auth=oauth2 exec -s "$SESSION" --timeout 36000 -f "$CARNET"
CODE=$?

echoetape "termine (code $CODE)"
if [ "$CODE" -eq 0 ]; then
    echo "Archive et empreinte : voir plus haut dans ce journal."
    echo "Rapatriez l'archive ET les sources avant d'effacer quoi que ce soit."
else
    echo "ECHEC. Avant de relancer, verifiez ce que la VM a REELLEMENT fait :"
    echo "  $CLI --auth=oauth2 status -s $SESSION"
    echo "un silence du client ne veut pas dire que la VM ne travaille pas."
fi
exit "$CODE"
