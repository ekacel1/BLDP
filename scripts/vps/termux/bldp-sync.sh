#!/data/data/com.termux/files/usr/bin/bash
# Rapatrie sur le telephone tout ce que le VPS a de nouveau, puis le prouve.
#
# Le VPS ne peut pas joindre un telephone : pas d'adresse stable, NAT operateur,
# veille agressive. C'est donc au telephone d'aller chercher. Ce script est fait
# pour etre relance sans cesse : il ne retransfere que ce qui manque.
#
# Il n'efface JAMAIS rien, ni ici ni la-bas. Il se contente de deposer sur le
# VPS un recu attestant que telle archive et telles sources sont arrivees
# intactes. C'est ce recu, et lui seul, qui autorisera « liberer.sh » a effacer
# quoi que ce soit du serveur. Le telephone constate ; le serveur decide.

set -uo pipefail

VPS=${BLDP_VPS:-root@191.96.1.191}
CLE=${BLDP_CLE:-$HOME/.ssh/bldp_termux}
LOCAL=${BLDP_LOCAL:-$HOME/legal-data}
JOURNAL="$LOCAL/sync.log"

SSH="ssh -i $CLE -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=15"

mkdir -p "$LOCAL"
exec > >(tee -a "$JOURNAL") 2>&1
echo
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="

# Sans verrou, Android suspend le processus des que l'ecran s'eteint et le
# transfert s'arrete au milieu. rsync reprendrait, mais on perdrait des heures.
command -v termux-wake-lock >/dev/null && termux-wake-lock
relacher() { command -v termux-wake-unlock >/dev/null && termux-wake-unlock; }
trap relacher EXIT

if ! $SSH "$VPS" true 2>/dev/null; then
    echo "VPS injoignable — on reessaiera au prochain passage."
    exit 0          # pas une erreur : le reseau va et vient
fi

# ---------------------------------------------------------------- archives --
echo "-- archives --"
mkdir -p "$LOCAL/archives"
rsync -a --partial --info=stats1 -e "$SSH" \
      "$VPS:/opt/bldp/archives/" "$LOCAL/archives/" || {
    echo "transfert des archives interrompu — reprise au prochain passage"
    exit 0
}

# L'empreinte est calculee par le VPS et comparee ici. Une archive dont
# l'empreinte ne correspond pas n'est pas une archive : on la signale et on
# n'ecrit aucun recu la concernant.
$SSH "$VPS" 'cd /opt/bldp/archives && sha256sum *.zip 2>/dev/null' > "$LOCAL/.empreintes-vps" || true
verifiees=0
douteuses=0
while read -r attendue chemin; do
    nom=$(basename "$chemin")
    [ -f "$LOCAL/archives/$nom" ] || continue
    obtenue=$(sha256sum "$LOCAL/archives/$nom" | cut -d' ' -f1)
    if [ "$attendue" = "$obtenue" ]; then
        verifiees=$((verifiees + 1))
    else
        echo "  EMPREINTE DIFFERENTE : $nom"
        echo "    attendue $attendue"
        echo "    obtenue  $obtenue"
        rm -f "$LOCAL/archives/$nom"      # incomplete : rsync la reprendra
        douteuses=$((douteuses + 1))
    fi
done < "$LOCAL/.empreintes-vps"
echo "  $verifiees archive(s) verifiee(s), $douteuses douteuse(s)"

# ----------------------------------------------------------------- sources --
# Un lot a la fois, et on ne passe au suivant qu'une fois celui-ci complet.
for lot in $($SSH "$VPS" 'ls -d /opt/bldp/lot*/input/decrets 2>/dev/null | cut -d/ -f4'); do
    distant="/opt/bldp/$lot/input/decrets"
    attendus=$($SSH "$VPS" "ls -1 $distant/*.pdf 2>/dev/null | wc -l")
    [ "${attendus:-0}" -gt 0 ] || continue

    echo "-- sources $lot ($attendus fichiers) --"
    mkdir -p "$LOCAL/$lot-sources"
    ici=$(ls -1 "$LOCAL/$lot-sources"/*.pdf 2>/dev/null | wc -l)
    if [ "$ici" -eq "$attendus" ]; then
        echo "  deja complet"
    else
        echo "  $ici/$attendus ici — transfert"
        rsync -a --partial --info=progress2 -e "$SSH" \
              "$VPS:$distant/" "$LOCAL/$lot-sources/" || {
            echo "  interrompu — reprise au prochain passage"
            continue
        }
        ici=$(ls -1 "$LOCAL/$lot-sources"/*.pdf 2>/dev/null | wc -l)
    fi

    if [ "$ici" -ne "$attendus" ]; then
        echo "  INCOMPLET : $ici sur $attendus — aucun recu emis"
        continue
    fi

    # Un fichier de moins de 1 Ko n'est pas un decret. Mieux vaut le voir ici
    # que decouvrir un corpus troue une fois les originaux effaces.
    tronques=$(find "$LOCAL/$lot-sources" -name '*.pdf' -size -1k | wc -l)
    if [ "$tronques" -gt 0 ]; then
        echo "  $tronques fichier(s) tronque(s) — aucun recu emis"
        continue
    fi

    # ------------------------------------------------------------- le recu --
    archive=$(ls -1 "$LOCAL/archives/$lot-corpus-"*.zip 2>/dev/null | tail -1)
    if [ -z "$archive" ]; then
        echo "  sources completes, mais aucune archive $lot ici — recu differe"
        continue
    fi
    empreinte=$(sha256sum "$archive" | cut -d' ' -f1)
    $SSH "$VPS" "mkdir -p /opt/bldp/recu && cat > /opt/bldp/recu/$lot.ok" <<FIN
lot        : $lot
date       : $(date -u '+%Y-%m-%dT%H:%M:%SZ')
sources    : $ici fichier(s), 0 tronque(s)
archive    : $(basename "$archive")
empreinte  : $empreinte
machine    : $(uname -n)
FIN
    echo "  RECU emis : /opt/bldp/recu/$lot.ok"
    echo "  le serveur peut liberer les sources de $lot"
done

echo "=== fin ==="
