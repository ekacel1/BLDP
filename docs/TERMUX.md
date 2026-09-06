# Automatiser le rapatriement sur le téléphone

## Le principe, et pourquoi il est dans ce sens

Un téléphone n'a pas d'adresse stable, vit derrière un NAT opérateur et suspend
ses processus dès que l'écran s'éteint. **Le VPS ne peut pas le joindre.** C'est
donc au téléphone d'aller chercher, à intervalle régulier.

La boucle se referme ainsi :

```
  VPS                              Téléphone
  ───                              ─────────
  archives + sources     ──rsync──→  copie locale
                                     ↓ vérifie empreinte + compte
  recu/<lot>.ok          ←──ssh────  reçu signé
      ↓
  liberer.sh efface — mais seulement s'il y a un reçu valide
```

**Le téléphone constate, le serveur décide.** Le script du téléphone n'efface
jamais rien, nulle part. Il dépose un reçu attestant que l'archive porte la
bonne empreinte, que tous les fichiers sources sont arrivés et qu'aucun n'est
tronqué. C'est ce reçu, et lui seul, qui autorise l'effacement côté serveur.

C'est la règle du §33 rendue mécanique : rien ne s'efface sur une supposition.

---

## Installation, une seule fois

### 1. Les paquets

```bash
pkg install -y openssh rsync termux-api
```

`termux-api` fournit `termux-wake-lock`, sans lequel Android suspend le
transfert dès l'extinction de l'écran. Installez aussi l'application
**Termux:API** depuis le même magasin que Termux — le paquet seul ne suffit pas.

### 2. Une clé, pour ne plus taper de mot de passe

Aujourd'hui le téléphone se connecte au VPS par mot de passe root : aucune
automatisation n'est possible ainsi.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/bldp_termux -N ""
cat ~/.ssh/bldp_termux.pub
```

Copiez la ligne affichée, puis sur le VPS :

```bash
printf 'no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty %s\n' \
  'COLLEZ_LA_LIGNE_ICI' >> /root/.ssh/authorized_keys
```

Les restrictions limitent cette clé au transfert de fichiers : ni tunnel, ni
terminal interactif, ni relais d'agent.

Vérifiez :

```bash
ssh -i ~/.ssh/bldp_termux -o IdentitiesOnly=yes root@191.96.1.191 'df -h / | tail -1'
```

### 3. Le script

```bash
mkdir -p ~/bin
scp -i ~/.ssh/bldp_termux root@191.96.1.191:/opt/bldp-exploitation/termux/bldp-sync.sh ~/bin/
chmod +x ~/bin/bldp-sync.sh
~/bin/bldp-sync.sh          # premier passage, à la main
```

Le premier passage rapatrie tout ce qui est en retard. Les suivants ne
transfèrent que le nouveau.

---

## Le faire tourner tout seul

Trois façons, de la plus économe à la plus simple.

### A. Le planificateur d'Android — recommandé

```bash
termux-job-scheduler \
    --script ~/bin/bldp-sync.sh \
    --period-ms 3600000 \
    --network unmetered \
    --persisted true
```

Une tentative par heure, **uniquement en Wi-Fi**, et ça survit au redémarrage.
C'est Android lui-même qui décide du moment exact, en fonction de la batterie —
d'où sa sobriété.

```bash
termux-job-scheduler --pending          # voir ce qui est programmé
termux-job-scheduler --cancel-all       # tout annuler
```

### B. cron

```bash
pkg install -y cronie
crontab -e
```

```
*/30 * * * * $HOME/bin/bldp-sync.sh
```

Puis, pour que cron démarre au lancement de Termux, avec le paquet
`termux-boot` installé :

```bash
mkdir -p ~/.termux/boot
printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\ncrond\n' \
  > ~/.termux/boot/demarrer && chmod +x ~/.termux/boot/demarrer
```

### C. Une boucle sous tmux — pour un gros rattrapage

```bash
termux-wake-lock
tmux new -s sync
while true; do ~/bin/bldp-sync.sh; sleep 900; done
```

`Ctrl-b` puis `d` pour détacher. La plus gourmande en batterie, mais la plus
directe quand il y a plusieurs gigaoctets en retard.

---

## Libérer le serveur

Le script du téléphone n'efface rien. Sur le VPS :

```bash
/opt/bldp-exploitation/liberer.sh                 # constat, n'efface rien
/opt/bldp-exploitation/liberer.sh --appliquer     # efface, reçus à l'appui
```

Il refuse au moindre écart : pas de reçu, un compte qui ne correspond pas, une
archive absente ou dont l'empreinte a changé. Chaque refus est motivé.

Pour l'automatiser complètement, un cron quotidien sur le VPS :

```
0 3 * * * /opt/bldp-exploitation/liberer.sh --appliquer >> /opt/bldp-exploitation/journaux/liberer.log 2>&1
```

> **À ne mettre en place qu'une fois le cycle éprouvé à la main.** Un effacement
> automatique de fichiers qui n'existent nulle part ailleurs mérite d'avoir été
> vu fonctionner plusieurs fois avant qu'on lui fasse confiance.

---

## Ce que ça ne fait pas, et pourquoi

**Pas de Syncthing.** L'outil conviendrait techniquement, mais il **réplique
les suppressions** : effacer les sources sur le VPS les effacerait sur le
téléphone. Or c'est exactement l'inverse qu'on veut — le téléphone est la copie
durable, le VPS n'est qu'un relais. `rsync` sans `--delete` ne peut pas se
tromper dans ce sens.

**Pas de poussée depuis le VPS.** Elle exigerait un serveur SSH sur le
téléphone et un tunnel permanent (Tailscale, ZeroTier). Plus de pièces, plus de
surface exposée, pour un gain nul : le téléphone qui tire fait le même travail.

**Aucun effacement côté téléphone.** Jamais, par aucun de ces scripts. Ce qui
arrive là reste là.
