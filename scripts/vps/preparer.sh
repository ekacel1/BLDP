#!/usr/bin/env bash
# Prepare le VPS a piloter les cycles Colab lui-meme. A executer UNE FOIS.
#
# Pourquoi depuis le VPS et non depuis un portable : le client « colab exec »
# envoie les cellules une par une et attend la reponse de chacune. Si sa
# connexion tombe — deux secondes de mise en veille suffisent — la VM continue
# a travailler mais plus personne ne lui envoie la suite. Le cycle s'arrete
# sans erreur, sans trace, et la VM finit reprise par Google. C'est arrive deux
# fois le 6 septembre 2026 ; la seconde a coute 90 minutes de traitement.
#
# Le VPS, lui, ne dort pas.

set -euo pipefail

RACINE=/opt/bldp-exploitation
CLI=/opt/colab-cli/bin/colab
CLE=/root/.ssh/colab_vm

echo "=== 1. Le CLI Colab ==="
if [ ! -x "$CLI" ]; then
    echo "ABSENT : $CLI"
    echo "  python3.12 -m venv /opt/colab-cli"
    echo "  /opt/colab-cli/bin/pip install google-colab-cli"
    exit 1
fi
if ! grep -q "_ClientNoyau" /opt/colab-cli/lib/python3.12/site-packages/colab_cli/runtime.py; then
    echo "NON CORRIGE : le CLI plantera sur « colab new »."
    echo "  /opt/colab-cli/bin/python $RACINE/patch_colab_cli.py \\"
    echo "      /opt/colab-cli/lib/python3.12/site-packages/colab_cli"
    exit 1
fi
echo "  present et corrige"

echo
echo "=== 2. La cle que la VM Colab utilisera pour joindre ce serveur ==="
# La VM doit pouvoir deposer ici les sources puis l'archive. On lui donne une
# cle dediee, qui n'ouvre que ce serveur et rien d'autre : pas de tunnel, pas
# de terminal, pas de transfert d'agent. Elle est generee ici meme, donc
# aucune cle privee ne transite par le reseau.
if [ ! -f "$CLE" ]; then
    ssh-keygen -q -t ed25519 -N "" -C "colab-vm vers bldp" -f "$CLE"
    echo "  cle creee : $CLE"
else
    echo "  cle deja presente : $CLE"
fi

RESTRICTIONS='no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty'
PUBLIQUE=$(cat "$CLE.pub")
if ! grep -qF "$PUBLIQUE" /root/.ssh/authorized_keys 2>/dev/null; then
    printf '%s %s\n' "$RESTRICTIONS" "$PUBLIQUE" >> /root/.ssh/authorized_keys
    echo "  autorisee, avec restrictions : $RESTRICTIONS"
else
    echo "  deja autorisee"
fi
chmod 600 /root/.ssh/authorized_keys

echo
echo "=== 3. Le depot ==="
git -C /opt/bldp fetch --quiet origin
git -C /opt/bldp pull --quiet --ff-only origin main
echo "  $(git -C /opt/bldp log --oneline -1)"

echo
echo "=== 4. L'authentification Google ==="
if $CLI --auth=oauth2 whoami >/dev/null 2>&1; then
    $CLI --auth=oauth2 whoami 2>&1 | head -3 | sed 's/^/  /'
else
    cat <<'FIN'
  PAS ENCORE AUTHENTIFIE.

  Lancez, et suivez le lien affiche :

      /opt/colab-cli/bin/colab --auth=oauth2 sessions

  Google affiche une URL ; ouvrez-la dans un navigateur, approuvez, puis
  collez le code renvoye. Le jeton est ecrit dans
  ~/.config/colab-cli/token.json avec un jeton de rafraichissement :
  l'operation ne se refait pas.
FIN
    exit 1
fi

echo
echo "Le VPS est pret. Lancez un cycle avec :"
echo "    $RACINE/lancer_tranche.sh"
