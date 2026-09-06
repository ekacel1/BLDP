# Relais — reprendre l'exploitation BLDP

> **Ce document se suffit à lui-même.** Il est écrit pour la personne — ou
> l'agent — qui reprend sans rien connaître de ce qui précède. Tout ce qu'il
> affirme a été vérifié le **6 septembre 2026**, y compris les chemins et les
> empreintes.
>
> Deux compléments dans le dépôt : [`README.md`](/opt/bldp/README.md) dit ce
> que fait BLDP, le [Cahier des charges](/opt/bldp/Cahier%20des%20charges%20—%20Benin%20Legal%20Data%20Pipeline.md)
> énonce les règles du projet.

---

## 0. À lire avant de toucher à quoi que ce soit

### La règle qui prime

> **Exactitude > automatisation.** En cas de doute : **ne pas deviner →
> signaler → validation humaine.** (§33 du Cahier des charges.)

Quand une étape hésite entre « continuer quand même » et « s'arrêter en le
disant », elle s'arrête. Si vous modifiez ces scripts, gardez ce sens :
**un échec bruyant vaut mieux qu'un succès faux.** Plusieurs des pannes du §7
étaient des succès faux — le pire cas.

### Les invariants

| Règle | Pourquoi |
|---|---|
| `input/` n'est **jamais** modifié | Les originaux sont la preuve ; tout le reste est dérivé et reproductible. |
| Aucun document réel n'entre dans le dépôt git | Le dépôt est public. |
| `privacy.allow_external_calls: false` par défaut | Aucun document ne quitte la machine sans consentement explicite. |
| Les clés d'API se lisent dans l'environnement, jamais dans un fichier | Un fichier de configuration finit commité. |
| **Rien n'est effacé avant vérification d'empreinte** | C'est la seule preuve que la copie rapatriée est intacte. |
| Une seconde entre deux requêtes au SGG | Correction envers une administration, et ce qui évite de se faire bloquer. |
| L'adresse de contact est `dikdokmoney@gmail.com` | Elle part dans le `User-Agent` de chaque requête. N'y mettez **jamais** l'adresse personnelle du responsable. |

### Le principe qui a déjà tout sauvé une fois

**Les sources partent sur le VPS avant que le traitement commence.** Le
6 septembre, une VM Colab a été reprise par Google en pleine exécution,
emportant 2 483 PDF et 90 minutes de calcul. Les mêmes 2 483 fichiers étaient
sur le VPS depuis une heure : seul le traitement était à refaire.

C'est aussi pourquoi la cellule de téléchargement regarde **d'abord** ce que le
VPS possède déjà, et ne sollicite le SGG que pour ce qui manque vraiment.

---

## 1. Où en est le corpus

| Lot | Pages d'index | Documents | Archive | Empreinte SHA-256 |
|---|---|---|---|---|
| lot 1 | 1 → ~150 | 2 555 | `lot1-corpus-20260904T200524Z.zip` (144 Mo) | `f4555483deb642a4a18a07266c8e2e22062156e9e3368605655f8cbf65f15d0e` |
| lot 2 | ~150 → 306 | 6 110 | `lot2-corpus-20260905T064429Z.zip` (154 Mo) | `84b1c9d269afe6900646cbcd7039366f6d0cdee9735b0abe0d0bee03cb855431` |
| lot 3 | 307 → 331 | 438 | `lot3-corpus-20260905T233947Z.zip` (10,7 Mo) | `237320711d7f7b73e271a83c19c77fcfa35d8c6cd1a8d5b2b53e31b4383d2b0c` |
| lot 4 | 332 → 481 | 2 483 | `lot4-corpus-20260906T113745Z.zip` (41,6 Mo) | `624b0f9c476ab1ba1bc5e14af2770099b5d19021cd985cc3a2016ea718e3b0c6` |

L'index des décrets du SGG compte environ **1 300 pages** ; il en restera
environ **820** après le lot 4 : il en reste ~7 tranches de 150 pages.

### Ce que contient une archive

Le corpus **traité** et le manifeste de reconstitution — pour chaque document,
son URL d'origine et l'empreinte du fichier source. **Elle ne contient pas les
PDF.** C'est pourquoi les sources doivent être rapatriées elles aussi : les
effacer sans les avoir copiées détruit les seuls originaux.

```
lot4/manifeste.json
lot4/exports/legal_database.sqlite      le corpus structuré
lot4/exports/documents.jsonl
lot4/exports/articles.jsonl
lot4/exports/metadata.json
lot4/exports/quality_report.json
lot4/traites/decrets/*.json             un fichier par document
```

---

## 2. Les machines

```
  VPS 191.96.1.191            VM Colab (éphémère)          Téléphone (Termux)
  ────────────────            ───────────────────          ──────────────────
  PILOTE le cycle       ──→   collecte + traitement   ──→  archives + sources
  stocke                      2 cœurs, 12 Go, 88 Go        copie durable
  allumé en permanence        détruite à la fin
```

**VPS** — `root@191.96.1.191`, Ubuntu 22.04, 97 Go dont **~17 Go libres**,
partagés avec cinq autres projets. C'est la ressource la plus contrainte, et
c'est désormais lui qui pilote.

**VM Colab** — compte `ekawecelestin@gmail.com`, **offre gratuite**. Mesuré :
2 cœurs (AMD EPYC 7B12), 12 Go de RAM, 88 Go de disque, Tesseract 4.1.1
préinstallé (le carnet installe la 5.x), ni Ghostscript ni OCRmyPDF.

> **Ne demandez jamais de GPU.** Mesuré le 6 septembre : `--gpu T4` donne les
> **mêmes 2 cœurs** et les mêmes 12 Go, sur un processeur plus ancien (Xeon
> @ 2,00 GHz) et avec 23 Go de disque en moins. Aucun outil de la chaîne —
> Tesseract, Ghostscript, PyMuPDF, unpaper — n'a de version CUDA : la carte
> resterait inutilisée en consommant un quota bien plus rare. Le GPU deviendra
> utile le jour où les embeddings seront activés, pas avant.

**Téléphone** — destination finale. Le rapatriement des sources est le
transfert le plus long ; c'est lui qui cadence le rythme des tranches.

---

## 3. Piloter depuis le VPS

### Pourquoi, et pas depuis un portable

`colab exec` **envoie les cellules une par une** et attend la réponse de
chacune : le carnet ne s'exécute pas tout seul sur Colab. Si la connexion du
client tombe, la VM continue son travail mais plus personne ne lui envoie la
suite. Le cycle s'arrête sans erreur et sans trace.

Le 6 septembre, **deux secondes de mise en veille** ont suffi, deux fois. La
seconde a coûté 90 minutes de traitement et la VM elle-même. Le VPS, lui, ne
dort pas.

### Où tout se trouve

```
/opt/bldp-exploitation/
    RELAIS.md              ce document
    preparer.sh            installation, une seule fois
    lancer_tranche.sh      un cycle complet
    patch_colab_cli.py     correctif du CLI, à réappliquer après mise à jour
    journaux/              un journal horodaté par cycle

/opt/colab-cli/            le CLI Colab, dans son propre Python 3.12
/opt/bldp/                 le dépôt git (branche main)
/opt/bldp/archives/        les archives produites
/root/.ssh/colab_vm        la clé que la VM utilise pour joindre ce serveur
~/.config/colab-cli/       jeton OAuth et état des sessions
```

### Préparation, une seule fois

```bash
/opt/bldp-exploitation/preparer.sh
```

Il vérifie le CLI et son correctif, crée la clé `colab_vm` si besoin et
l'autorise avec restrictions, met le dépôt à jour, puis contrôle
l'authentification. S'il manque l'authentification, suivez ce qu'il affiche :

```bash
/opt/colab-cli/bin/colab --auth=oauth2 sessions
```

Une URL s'affiche ; ouvrez-la dans un navigateur, approuvez, collez le code.
Le jeton est enregistré avec un jeton de rafraîchissement — **l'opération ne se
refait pas.** La portée `colaboratory` est indispensable : sans elle, le
maintien en vie des VM échoue par un 403 et Colab reprend la machine en route.

### Un cycle

```bash
# 1. Régler la tranche : PAGE_DEBUT, PAGE_FIN, NOM_LOT dans le carnet
nano /opt/bldp/notebooks/collecte_traitement_sgg.ipynb   # ou éditer et pousser

# 2. Lancer — sous tmux, survit à la fermeture du terminal
/opt/bldp-exploitation/lancer_tranche.sh

# 3. Suivre
tmux attach -t bldp-<horodatage>        # Ctrl-b puis d pour détacher
tail -f /opt/bldp-exploitation/journaux/bldp-<horodatage>.log
```

> **Le carnet est lu depuis `origin/main`**, pas depuis une copie locale. Si
> vous changez la tranche, **poussez** avant de lancer, ou éditez directement
> `/opt/bldp/notebooks/...` — le script fait un `git pull --ff-only`, qui
> échouera sur une modification locale non commitée.

Le script rend toujours la VM en sortant, y compris sur erreur ou
interruption : rien ne la libère avant 24 h, et une VM oubliée consomme du
quota pour rien.

---

## 4. Ce que fait le carnet, cellule par cellule

| # | Étape | Ce qui peut mal tourner |
|---|---|---|
| 1 | apt + Tesseract 5 (PPA `alex-p/tesseract-ocr5`), clone du dépôt, `pip install -e .[ocr]` | Colab livre Tesseract **4.1.1** ; le corpus est en **5.x**. Si le PPA échoue, la cellule le **dit** : deux moteurs sur un même corpus produisent des textes qui ne se comparent pas. `[ocr]` est un extra — sans lui, le pipeline meurt à la première page à océriser, des heures après le lancement. |
| 2 | Paramètres de tranche, estimation du cycle | L'estimation ne compte **que** le délai de politesse, pas le transfert. Elle sous-estime. |
| 3 | Installe la clé, vérifie la place sur le VPS | Lève si la clé est illisible ou si la marge manque. |
| 4 | Recense l'index | Affiche `page N/FIN — M fiches cumulees`. |
| 5 | **Rapatrie du VPS ce qu'il a déjà**, puis télécharge le reste au SGG | Progression toutes les 30 s sur tous les chemins. Contrôle de l'en-tête `%PDF` : venir du VPS ne vaut pas preuve. |
| 6 | **Expédie les sources vers le VPS** | **Lève** si le compte ne tombe pas juste des deux côtés. À partir d'ici, le traitement peut tomber sans rien coûter. |
| 7 | Construit le catalogue LCF | Fournit `source_url` et permet de **confronter** les métadonnées extraites à celles annoncées par le SGG — confronter, pas écraser. |
| 8 | Écrit `config.yaml` | Fixe le nombre de fils (§5). |
| 9 | **Lance le pipeline** | Affiche à la fin le **débit mesuré en s/document** et la taille de tranche tenable. |
| 10 | Audit | Compte les champs vides. Un taux qui grimpe signale une source qui a changé de forme. |
| 11 | Empaquette | Corpus + manifeste de reconstitution. |
| 12 | Expédie l'archive | **Lève** si la place manque. |
| 13 | **Compare les empreintes** | **Lève** si elles diffèrent. C'est le verrou qui autorisera l'effacement. |

---

## 5. Le réglage qui change le plus

**Nombre de fils.** L'ancienne règle `max(1, coeurs - 1)` donnait **1 fil** sur
une VM Colab, qui n'a que deux cœurs : un cœur restait inoccupé du début à la
fin. Garder un cœur « pour le système » est sain sur une grosse machine et
absurde sur deux.

```python
fils = coeurs if coeurs <= 2 else coeurs - 1
```

Le travail lourd (`ocrmypdf`, `tesseract`, `gs`) se fait dans des
**sous-processus** : les fils Python passent leur temps à les attendre, GIL
relâché, donc deux fils sur deux cœurs se recouvrent réellement. Le pipeline
force de lui-même `ocr.jobs = 1` dès que `workers > 1`.

Effet mesuré : **1,1 s/document**, contre 4,02 s/doc sur le VPS.

### Pourquoi pas PaddleOCR sur GPU

Mesuré sur le lot 2 (6 110 documents) : `pymupdf` 78,8 %, `ocr` 20,4 %,
`mixed` 0,8 %. L'OCR ne touche **qu'un document sur cinq mais consomme
l'essentiel du temps**. Un moteur GPU diviserait le cycle par ~4 : l'argument
de performance est réel.

Ce n'est pas la performance qui fait renoncer : **11 000 documents sont déjà
océrisés sous Tesseract.** Un corpus moitié Tesseract moitié Paddle n'est plus
comparable à lui-même — erreurs différentes, découpage en articles différent —
et rien dans les métadonnées ne dirait lequel a produit quoi.

C'est une décision **de corpus**, pas une optimisation de tranche : elle
impliquerait de tout retraiter, après comparaison contrôlée sur échantillon. Le
carnet `notebooks/paddleocr_vs_tesseract.ipynb` existe pour cela — et **lui** a
besoin d'un `--gpu T4`.

---

## 6. Dimensionner une tranche

Mesures du lot 3 puis du lot 4 :

| Grandeur | Mesure |
|---|---|
| Traitement | **1,1 à 1,2 s/document** sur 2 fils — confirmé sur 438 puis 2 483 documents |
| Documents par page d'index | **16,5 à 17,5** |
| Sources | **1,24 à 1,72 Mo/document**, soit ~25 Mo par page |
| Archive | **~24,5 Ko/document** — 10,7 Mo pour 438 documents |
| Téléchargement au SGG | **~1,8 s/document** |

**Ce n'est plus le calcul qui borne, c'est le disque du VPS.** 17 Go libres
moins 8 Go de marge laissent ~9 Go de sources, soit **~360 pages** au maximum
absolu. Le lot 4 en a pris 150.

Noter la disproportion : les **sources** pèsent 25 Mo par page, l'**archive**
0,4 Mo. Ce sont les sources qui saturent, et elles ne servent qu'à pouvoir
retraiter sans recollecter.

---

## 7. Ce qui a déjà échoué

Chaque ligne a coûté du temps. Elles sont là pour qu'il ne soit pas repayé.

| Symptôme | Cause réelle | Remède |
|---|---|---|
| **La sortie se tait alors que la VM travaille** | Une **mise en veille du portable, même de deux secondes**, tue la connexion qui relaie la sortie du noyau. La VM continue sans le savoir ; le client n'apprendra jamais que la cellule est finie et n'enverra jamais les suivantes. Vérifiable : `Get-WinEvent -FilterHashtable @{LogName='System'; Id=42,107}`. | **Piloter depuis le VPS** (§3). En dépannage sur portable : un verrou `SetThreadExecutionState`. |
| **La VM disparaît ; `status` affiche encore BUSY** | Une VM gratuite est fournie « au mieux » et peut être reprise à tout moment. `status` lit l'état local, pas la machine : **il ment**. Les vrais signes sont un `404` sur `/api/kernels`, un `colab ls` qui ne trouve même pas `/content`, un `download` qui échoue sur un fichier qu'on vient d'y déposer. | Rien de ce qui est sur la VM ne survit. Les sources sont sur le VPS, et la cellule 5 les rapatrie de là. |
| `TimeoutError: Timeout waiting for output` | Le client abandonne quand une cellule reste muette ; l'installation l'est plusieurs minutes. **Le noyau, lui, avait réussi.** | `--timeout 36000`. **Vérifier l'état réel de la VM avant de tout relancer.** |
| `Upload failed: 500` sur `colab upload` | Sous Windows, Git Bash (MSYS) traduit `/content/...` en `C:/Program Files/Git/content/...` | PowerShell, ou le VPS. |
| `Load key : error in libcrypto` | Un champ de saisie écrase les 7 lignes d'une clé OpenSSH en une seule. Vérifié : intacte → OK, CRLF → récupérable, **aplatie → irrécupérable, avec exactement ce message**. | `colab upload` : le fichier passe octet pour octet. |
| `module 'bldp' has no attribute '__version__'` | Un essai raté laisse `/content/bldp` vide ; Python en fait un paquet-espace-de-noms et le **cache dans `sys.modules`**. L'import suivant rend ce fantôme sans relire le disque. | La cellule 1 purge `sys.modules`, appelle `invalidate_caches()` et vérifie `bldp.__file__`. |
| `AttributeError: module 'jupyter_kernel_client' has no attribute 'KernelClient'` | Bogue amont : le CLI n'épingle pas sa dépendance, et la 1.0.2 a renommé la classe. | `patch_colab_cli.py`. **À réappliquer après toute mise à jour du CLI.** |
| `ModuleNotFoundError: termios` | Sous Windows seulement : modules Unix. Ne concerne que `colab console`. | Sans objet sur le VPS. |
| `UnicodeEncodeError: 'charmap'` | La console Windows parle **cp1252** : `empaqueter_lot.py` mourait **après** avoir écrit l'archive, en laissant croire à un échec. | `reconfigure(encoding="utf-8")` ; `PYTHONIOENCODING=utf-8`. |
| Un `rsync` qui « réussit » sans rien transférer | Un **`\n` littéral** au lieu d'une continuation de ligne. Bash y lisait un `n` : rsync copiait dans un répertoire local nommé `n`, puis la ligne de destination s'exécutait comme une commande séparée. | Les `!commande` sont remplacées par des appels vérifiés qui lèvent. |
| Progression qui cesse de s'afficher | Le `print` était dans la branche de succès, après deux `continue` : un rejet ne laissait aucune trace. | Affichage à intervalle de **temps**, sur tous les chemins. |
| 15,6 Go effacés, 2 Go récupérés | Les `input/` étaient des **liens durs** vers `/var/lib/lcf/data/objects`. Effacer un lien ne libère rien tant que l'original vit. | Vérifier `df` avant/après, et `find -printf '%n'` pour le compte de liens. |
| Le disque du VPS se remplit tout seul | Le démon LCF balayait `bj.sgg.decrets` et aurait tiré ~26 000 décrets (~50 Go). | Source désactivée — §9. |

### La leçon transversale

**Un silence du client ne dit rien sur la VM.** Avant de conclure à un blocage,
faites l'autopsie : comptez les fichiers, regardez leurs horodatages, testez le
réseau depuis la VM. J'ai arrêté une session parfaitement saine pour avoir
lu « BUSY » comme « coincé » alors qu'il voulait dire « elle travaille ».

---

## 8. Après le cycle : rapatrier, vérifier, effacer

**L'ordre compte, et les deux vérifications aussi.** Sur le téléphone :

```bash
termux-wake-lock
tmux new -s dl                       # Ctrl-b puis d pour détacher
```

**1. L'archive** — quelques dizaines de Mo :

```bash
until rsync -avP root@191.96.1.191:/opt/bldp/archives/lot4-corpus-*.zip ~/legal-data/; do
  sleep 15
done
sha256sum ~/legal-data/lot4-corpus-*.zip     # comparer à l'empreinte du journal
```

**2. Les sources** — plusieurs Go, l'étape longue :

```bash
until rsync -avP root@191.96.1.191:/opt/bldp/lot4/input/decrets/ ~/legal-data/lot4-sources/; do
  sleep 15
done
ls -1 ~/legal-data/lot4-sources/*.pdf | wc -l     # doit égaler le compte du journal
```

**3. Seulement alors**, libérez le VPS :

```bash
ssh root@191.96.1.191 'rm -rf /opt/bldp/lot4/input/decrets && df -h / | tail -1'
```

Puis avancez `PAGE_DEBUT`, `PAGE_FIN`, `NOM_LOT`, et relancez.

> **Ne lancez pas la tranche suivante avant d'avoir libéré la place.**

---

## 9. État des services sur le VPS

```bash
systemctl status lcf
cat /opt/lcf/lcf.config.json          # sauvegarde : lcf.config.json.avant-colab
```

**`bj.sgg.decrets` est désactivé** (`"enabled": false`, cron `30 21 * * *`) :
il remplirait exactement la place dont les tranches Colab ont besoin. Les cinq
autres sources restent actives.

```
bj.sgg.lois         → true
bj.sgg.decrets      → FALSE   ← désactivée délibérément
bj.sgg.ordonnances  → true
bj.sgg.arretes      → true
bj.sgg.accords      → true
bj.sgg.decisions    → true
```

Pour revenir en arrière : `enabled: true`, puis `systemctl restart lcf`.

---

## 10. Ce qui reste sur le VPS

| Emplacement | Contenu | Statut |
|---|---|---|
| `archives/` | 3 archives, 309 Mo | Le produit. |
| `lot1/data/processed/ocr` | 693 PDF océrisés | **À garder** : documents marqués « à vérifier », seuls témoins visuels de ce que la machine a lu. |
| `lot2/data/processed/ocr` | 371 PDF océrisés | **À garder**, même raison. |
| `lot2/reste` | **1 110 PDF sources, 1,8 Go** | **Résidu** — les documents traités sur le portable, jamais passés par `lot2/input`. À effacer **si et seulement si** une copie existe sur le téléphone. Vérifier avant. |
| `lot4/input/decrets` | 2 483 PDF, 3,0 Go | En attente du rapatriement et des deux vérifications. |

---

## 11. Points ouverts

| Sujet | État |
|---|---|
| **Zip du lot 1** | Toujours là (144 Mo). Sa suppression a été demandée puis non confirmée. |
| **`lot2/reste`** | 1,8 Go, le plus gros poste effaçable. Attend une vérification côté téléphone. |
| **`/var/lib/lcf/data/objects`** (16 Go) | Magasin réel du collecteur. L'effacer sortirait de ce qui a été autorisé, et la réaction du démon à des objets manquants n'a **pas** été vérifiée. `/var/lib/docker` (21 Go) est l'autre gros poste. |
| **Correctif `staleStreak` du LCF** | Non vérifié en profondeur. Index à 8 666 documents, rien collecté depuis le 2 septembre 2026 — et `bj.sgg.decrets` étant désactivé, il ne se vérifiera pas seul. |
| **Fichier PHP obfusqué** | Servi par `https://sgg.gouv.bj/doc/decret-2019-545/download`. À signaler au SGG. |
| **Tesseract 5.5.1 vs 5.4** | La VM installe 5.5.1, les lots 1 et 2 ont été produits en 5.4. Écart de version corrective — sans commune mesure avec le 4.1.1 évité, mais il existe. |
| **Estimations de la cellule 2** | Ne comptent que le délai de politesse, jamais le transfert. Sous-estiment le cycle. |
| **Délai de lecture sans échéance** | `lire()` utilise `timeout=60`, qui est un délai de **socket** : un serveur répondant au compte-gouttes ne le déclenche jamais. Aucune échéance totale. |

---

## 12. Aide-mémoire

```bash
CLI=/opt/colab-cli/bin/colab

# état
$CLI --auth=oauth2 whoami                    # identité et portées
$CLI --auth=oauth2 sessions                  # sessions actives côté serveur
$CLI --auth=oauth2 status  -s <nom>          # matériel, IDLE/BUSY — PEUT MENTIR
$CLI --auth=oauth2 log     -s <nom> -n 20    # journal, après un échec

# cycle
/opt/bldp-exploitation/lancer_tranche.sh
tmux ls
tail -f /opt/bldp-exploitation/journaux/*.log

# récupération
$CLI --auth=oauth2 restart-kernel -s <nom>   # noyau bloqué, VM conservée
$CLI --auth=oauth2 exec -s <nom> --timeout 900 -f sonde.py   # inspecter sans tout relancer
$CLI --auth=oauth2 stop -s <nom>             # TOUJOURS, sinon la VM vit 24 h

# stockage
df -h / | tail -1
ls -la /opt/bldp/archives/
cd /opt/bldp/archives && sha256sum *.zip
```

> **Après un échec, ne relancez pas à l'aveugle.** L'état du noyau survit à la
> déconnexion du client : une cellule peut très bien avoir abouti pendant que
> le client abandonnait. Inspectez d'abord.
