# Benin Legal Data Pipeline (BLDP)

Transforme des documents juridiques bruts du Bénin (PDF natifs ou scannés) en un
**corpus juridique propre, structuré et auditable**, exploitable par un futur
système de recherche/RAG.

> **Principe fondamental : exactitude > automatisation.**
> En cas de doute, le pipeline ne devine pas. Il signale, et demande une
> validation humaine.

[![Tests](https://img.shields.io/badge/tests-865%20passants-brightgreen)](#tests)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#installation)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-lightgrey)](LICENSE)

---

## Sommaire

- [Ce que fait BLDP](#ce-que-fait-bldp)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Collecter le corpus](#collecter-le-corpus)
- [Architecture](#architecture)
- [Formats supportés](#formats-supportés)
- [Formats de sortie](#formats-de-sortie)
- [Configuration](#configuration)
- [Exemples](#exemples)
- [Traçabilité et validation humaine](#traçabilité-et-validation-humaine)
- [Relecture assistée par un modèle](#relecture-assistée-par-un-modèle)
- [Limites connues](#limites-connues)
- [Autres juridictions](#autres-juridictions)
- [Tests](#tests)
- [Contribuer](#contribuer)
- [Licence](#licence)

---

## Ce que fait BLDP

```text
Documents juridiques officiels (PDF)
             ↓
       BLDP Pipeline
             ↓
   Corpus juridique structuré
             ↓
      Recherche / RAG
```

À partir d'un dossier de PDF, BLDP :

1. **inventorie** les documents et calcule leur empreinte ;
2. **décide** si un OCR est nécessaire, avec une justification et un score de
   confiance ;
3. **extrait** le texte page par page, en conservant la page d'origine ;
4. **nettoie** les artefacts techniques sans jamais toucher au contenu
   juridique ;
5. **détecte** la hiérarchie (titre, chapitre, section…) et les articles ;
6. **extrait** les métadonnées, chacune accompagnée de sa preuve ;
7. **repère** les relations entre textes (modifie, abroge, remplace) ;
8. **signale** les doublons sans jamais supprimer ;
9. **note** la qualité et oriente vers la validation humaine ;
10. **exporte** en JSONL, JSON, CSV et SQLite ;
11. **produit** optionnellement chunks, embeddings et index FAISS.

Tout fonctionne **localement**, sans GPU, sans service payant, et **aucun
document n'est envoyé à une API externe**.

---

## Installation

### Prérequis

- Python 3.11 ou supérieur
- 16 Go de RAM recommandés, ~40 Go d'espace disque pour un corpus important
- Pas de GPU nécessaire

### Installation minimale

Suffisante pour tout le pipeline sur des PDF contenant déjà du texte :

```bash
git clone https://github.com/OWNER/bldp.git
cd bldp
python -m pip install -e .
python -m bldp doctor
```

### Extras optionnels

| Extra          | Commande                            | Apporte |
|----------------|-------------------------------------|---------|
| OCR            | `pip install -e ".[ocr]"`           | traitement des PDF scannés |
| Embeddings     | `pip install -e ".[embeddings]"`    | vecteurs Sentence Transformers |
| FAISS          | `pip install -e ".[faiss]"`         | index vectoriel |
| Interface web  | `pip install -e ".[web]"`           | interface locale de validation |
| Développement  | `pip install -e ".[dev]"`           | suite de tests |
| Tout           | `pip install -e ".[all]"`           | l'ensemble |

### Binaires système pour l'OCR

L'extra `ocr` a besoin de deux programmes installés séparément :

- **Tesseract OCR** avec le paquet de langue française (`fra`)
- **Ghostscript** (requis par OCRmyPDF)

```bash
# Debian / Ubuntu
sudo apt install tesseract-ocr tesseract-ocr-fra ghostscript

# macOS
brew install tesseract tesseract-lang ghostscript
```

<details>
<summary><b>Windows — procédure détaillée (sans droits administrateur)</b></summary>

```powershell
# 1. Tesseract (winget installe uniquement l'anglais)
winget install --id UB-Mannheim.TesseractOCR --accept-package-agreements

# 2. Pack de langue française, dans un dossier utilisateur inscriptible
$tessdata = "$env:LOCALAPPDATA\tessdata"
New-Item -ItemType Directory -Force $tessdata | Out-Null
Copy-Item "C:\Program Files\Tesseract-OCR\tessdata\*" $tessdata -Recurse -Force
Invoke-WebRequest `
  "https://github.com/tesseract-ocr/tessdata/raw/main/fra.traineddata" `
  -OutFile "$tessdata\fra.traineddata"

# 3. Ghostscript (absent de winget) — releases officielles Artifex
#    https://github.com/ArtifexSoftware/ghostpdl-downloads/releases
#    Installation silencieuse dans un dossier utilisateur :
#    .\gsXXXXw64.exe /S /D=$env:LOCALAPPDATA\Ghostscript

# 4. Variables d'environnement persistantes
[Environment]::SetEnvironmentVariable("TESSDATA_PREFIX", $tessdata, "User")
$p = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path",
  "$p;C:\Program Files\Tesseract-OCR;$env:LOCALAPPDATA\Ghostscript\bin", "User")
```

Deux pièges rencontrés en pratique :

- **`TESSDATA_PREFIX` doit contenir les sous-dossiers `configs/` et
  `tessconfigs/`**, pas seulement les fichiers `.traineddata`. Sans eux,
  OCRmyPDF échoue sur `TesseractConfigError`. D'où le `Copy-Item -Recurse`
  ci-dessus.
- Ouvrez un **nouveau terminal** après l'étape 4 : les variables persistantes
  ne s'appliquent pas à la session en cours.

</details>

`python -m bldp doctor` indique précisément ce qui manque, langues installées
comprises.

---

## Utilisation

### Traitement complet

```bash
# 1. Déposez vos PDF (le classement en sous-dossiers est facultatif)
cp mes_documents/*.pdf input/lois/

# 2. Lancez le pipeline
python -m bldp pipeline ./input

# 3. Passez en revue ce qui est douteux
python -m bldp validate

# 4. Examinez un document en détail
python -m bldp validate --document loi_2026_001

# 5. Enregistrez votre décision
python -m bldp validate --document loi_2026_001 --set-status valide
```

### Toutes les commandes

| Commande   | Rôle |
|------------|------|
| `pipeline` | traitement complet, de l'import aux exports |
| `ingest`   | inventorier un dossier (empreintes, identifiants) |
| `analyze`  | dire quels PDF nécessitent un OCR, et pourquoi |
| `extract`  | extraire le texte d'un PDF (diagnostic) |
| `parse`    | extraire + nettoyer + découper en articles (diagnostic) |
| `process`  | traiter sans exporter, vers `data/processed/` |
| `validate` | passer en revue la qualité, enregistrer une décision |
| `suivi`    | tickets, étapes, journal — qui a fait quoi, et quand |
| `relire`   | relecture assistée par un modèle (envoi hors machine) |
| `embed`    | générer les embeddings et l'index vectoriel |
| `search`   | rechercher dans le corpus (plein texte ou vectoriel) |
| `trace`    | remonter d'un article à sa page et son fichier source |
| `export`   | réexporter le corpus enregistré |
| `stats`    | état du corpus |
| `serve`    | interface web locale |
| `doctor`   | diagnostic de l'environnement |
| `config`   | afficher la configuration effective |

Chaque commande accepte `--config`, `--set clé=valeur`, `--root`, `--log-level`
et `-q`.

### Traiter un gros corpus

Trois options changent tout dès qu'on dépasse quelques dizaines de documents :

```bash
python -m bldp pipeline ./input --resume --workers 4 --keep-ocr review
```

| Option | Effet |
|---|---|
| `--resume` | saute les documents déjà traités **avec succès**. Une interruption ne fait plus tout recommencer ; un document en échec est toujours retenté. |
| `--workers N` | traite N documents de front (`0` = un par cœur). Mesuré : ×1,9 sur 4 fils. |
| `--keep-ocr review` | ne conserve les PDF OCRisés que pour les documents à vérifier — l'auditabilité là où elle sert, sans saturer le disque. |

Les mêmes réglages existent en configuration :

```yaml
pipeline:
  resume: true
  workers: 4
ocr:
  keep_sidecar_for: review
```

Trois points à connaître :

- **Le résultat est identique en parallèle et en séquentiel.** L'ordre des
  documents suit l'entrée, jamais l'ordre d'achèvement : deux exécutions du
  même lot produisent le même corpus.
- **Avec `--workers > 1`, OCRmyPDF est bridé à un fil interne.** Sans cela, les
  deux niveaux de parallélisme se multiplient et saturent la machine au lieu de
  l'accélérer.
- **Avec `--resume`, les exports sont régénérés depuis la base complète.**
  N'exporter que le lot courant tronquerait `documents.jsonl` au dernier lot.

Ordre de grandeur mesuré : **1,8 s/page** en séquentiel. Comptez ~70 Go de PDF
OCRisés pour 10 000 documents si vous les conservez tous — d'où `--keep-ocr`.

### Suivre les documents : tickets, étapes, journal

Le pipeline sait traiter un document ; il ne sait pas dire **où on en est**
avec lui. Le registre de suivi tient ce rôle.

```bash
python -m bldp suivi etat                     # tableau de bord par étape
python -m bldp suivi liste --etape a_verifier # la file de relecture
python -m bldp suivi assigner loi_2025_09 virgile
python -m bldp suivi avancer loi_2025_09 valide --par virgile --motif "conforme au JO"
python -m bldp suivi montrer loi_2025_09      # la fiche et son journal complet
```

Chaque document reçoit un **ticket** (`BLDP-000042`) et un **badge** :

| badge | étape | ce qu'il reste à faire |
|---|---|---|
| `[ ]` | importé | traiter |
| `[*]` | traité | rien, sauf contrôle |
| `[!]` | à vérifier | relire — la qualité a signalé quelque chose |
| `[?]` | en revue | quelqu'un s'en occupe |
| `[V]` | validé | archiver |
| `[X]` | rejeté | — |

Trois propriétés structurent le registre :

- **Un contenu, un ticket.** Le ticket est attaché à l'empreinte du fichier,
  pas à son nom. Le même texte reçu deux fois — sous deux noms, dans deux
  dossiers, à six mois d'écart — retrouve son ticket, son historique et la
  décision humaine déjà prise. C'est ce qui évite de refaire un travail déjà
  fait, y compris le travail *humain*, que le pipeline ne peut pas deviner.
- **Un journal en écriture seule.** Chaque changement est consigné avec sa
  date, son auteur et son motif. On peut toujours répondre à « qui a validé ce
  document, quand, et pourquoi ? ».
- **Rien ne s'auto-valide.** `valide` et `rejete` exigent `--par <personne>` :
  le pipeline peut proposer `à vérifier`, jamais conclure (§16).

Avec `--resume`, un document déjà validé ou rejeté est écarté au même titre
qu'un document déjà traité : le rejouer remplacerait une décision humaine par
un verdict automatique.

### Interface web

```bash
pip install -e ".[web]"
python -m bldp serve
# http://127.0.0.1:8000
```

Elle permet de déposer un PDF, suivre son traitement, comparer le texte
d'origine au texte extrait, valider, et télécharger les exports. Aucune
ressource externe n'est chargée.

---

## Collecter le corpus

Le pipeline traite les PDF qu'on lui donne. Reste à les obtenir : le corpus
béninois se construit **tranche par tranche** depuis l'index du Secrétariat
Général du Gouvernement, et cette chaîne-là a ses propres règles.

```
  VPS                          VM Colab (éphémère)          Téléphone
  ───                          ───────────────────          ─────────
  pilote le cycle       ──→    collecte + traitement  ──→   copie durable
  stocke                       2 cœurs, détruite ensuite
  allumé en permanence
```

Une commande suffit, depuis le serveur :

```bash
/opt/bldp-exploitation/lancer_tranche.sh
```

Elle met le dépôt à jour, alloue une VM Colab, y exécute
[`notebooks/collecte_traitement_sgg.ipynb`](notebooks/collecte_traitement_sgg.ipynb),
puis rend la VM — y compris en cas d'erreur.

### Trois règles qui viennent de l'expérience

**Les sources partent sur le serveur avant que le traitement commence.** Une VM
Colab gratuite est fournie « au mieux » et peut disparaître en pleine
exécution. C'est arrivé : 2 483 PDF et 90 minutes de calcul perdus d'un coup.
Les mêmes fichiers étaient déjà sur le VPS ; seul le traitement fut à refaire,
et la reprise les a repris de là **sans redemander un seul document au SGG**.

**Rien ne s'efface avant vérification d'empreinte.** L'archive contient le
corpus traité et le manifeste de reconstitution — **pas les PDF**. Effacer les
sources sans les avoir rapatriées détruirait les seuls originaux. Le
rapatriement dépose un reçu attestant empreinte et comptes ; `liberer.sh`
n'efface que sur présentation de ce reçu, et refuse au moindre écart.

**Une seconde entre deux requêtes.** Ce n'est pas négociable : c'est ce qui est
correct envers une administration, et ce qui évite de se faire bloquer.

### Rapatrier sur le téléphone

Le VPS n'est qu'un relais : la copie durable est sur le téléphone. Mais **le
serveur ne peut pas joindre un téléphone** — pas d'adresse stable, NAT
opérateur, veille agressive. C'est donc au téléphone d'aller chercher.

Deux transferts, dans cet ordre, sous Termux :

```bash
termux-wake-lock
tmux new -s dl

# 1. l'archive — quelques dizaines de Mo
until rsync -avP root@191.96.1.191:/opt/bldp/archives/lot5-corpus-*.zip ~/legal-data/; do
  sleep 15
done
sha256sum ~/legal-data/lot5-corpus-*.zip

# 2. les sources — plusieurs Go, l'étape longue
until rsync -avP root@191.96.1.191:/opt/bldp/lot5/input/decrets/ ~/legal-data/lot5-sources/; do
  sleep 15
done
ls -1 ~/legal-data/lot5-sources/*.pdf | wc -l
```

#### Pourquoi chaque morceau

| Élément | Ce qu'il empêche |
|---|---|
| `termux-wake-lock` | Android suspend les processus dès que l'écran s'éteint. Sans lui, un transfert de 4 Go s'arrête au premier verrouillage et ne reprend qu'au prochain déverrouillage. |
| `tmux new -s dl` | La session survit à la fermeture de Termux et au passage en arrière-plan. `Ctrl-b` puis `d` détache ; `tmux attach -t dl` revient. |
| `until … done` | `rsync` sort en erreur quand la connexion tombe — ce qui arrive sur un réseau mobile. La boucle relance jusqu'à ce qu'il réussisse. Sans elle, un transfert de 4 Go échoue sur une coupure de trois secondes. |
| `sleep 15` | Attendre avant de relancer. Sans pause, une coupure durable devient une rafale de connexions, et le serveur bannit l'adresse pour excès de tentatives. |
| `-a` | Mode archive : récursif, et préserve dates et permissions. |
| `-P` | `--partial` **garde le fichier incomplet** pour que la tentative suivante reprenne où elle en était, au lieu de tout retélécharger ; `--progress` montre où on en est. C'est ce qui rend la boucle `until` supportable. |
| **pas de `--delete`** | Ne jamais répliquer les suppressions. Le VPS efface ses sources une fois rapatriées ; si le téléphone reflétait ces suppressions, il effacerait les originaux qu'il vient de sauver. |
| `sha256sum` | L'empreinte est la **seule** preuve que l'archive est arrivée intacte. C'est elle qui autorise l'effacement côté serveur — un transfert « qui a l'air fini » n'est pas une preuve. |
| `ls … \| wc -l` | Même rôle pour les sources : le compte de fichiers atteste qu'aucun ne manque. Une archive vérifiée ne dit rien des PDF, qu'elle ne contient pas. |

**Et surtout, l'ordre.** L'archive porte le corpus traité et le manifeste de
reconstitution — **pas les PDF**. Effacer les sources du serveur sans les avoir
rapatriées détruit les seuls originaux. D'où la règle : les deux transferts,
les deux vérifications, et seulement ensuite l'effacement.

#### Le faire tourner tout seul

Ces commandes à la main deviennent vite pénibles, une tranche toutes les deux
heures. `bldp-sync.sh` les enchaîne, vérifie, puis dépose sur le VPS un **reçu**
attestant l'empreinte de l'archive et le compte des sources.

```bash
termux-job-scheduler --script ~/bin/bldp-sync.sh \
    --period-ms 3600000 --network unmetered --persisted true
```

Une tentative par heure, **en Wi-Fi uniquement**, qui survit au redémarrage —
c'est Android qui choisit le moment selon l'état de la batterie.

Côté serveur, `liberer.sh` n'efface que sur présentation d'un reçu valide, et
refuse au moindre écart : reçu absent, compte qui ne correspond pas, archive
disparue, empreinte changée. **Le téléphone constate, le serveur décide.** C'est
le §33 du cahier des charges rendu mécanique : rien ne s'efface sur une
supposition.

Le détail de l'installation est dans [`docs/TERMUX.md`](docs/TERMUX.md).

### Où lire la suite

| Document | Ce qu'il couvre |
|---|---|
| [`docs/RELAIS.md`](docs/RELAIS.md) | **Tout, pour reprendre sans rien connaître** : état du corpus, installation, cycle pas à pas, catalogue des pannes rencontrées avec leur cause réelle. |
| [`docs/EXPLOITATION.md`](docs/EXPLOITATION.md) | Le même terrain vu depuis un poste de travail. |
| [`docs/TERMUX.md`](docs/TERMUX.md) | Automatiser le rapatriement sur téléphone, et le reçu qui autorise l'effacement. |
| [`scripts/vps/`](scripts/vps/) | Les scripts eux-mêmes. |

### Ce qu'on sait du terrain

Mesuré sur une VM Colab gratuite, deux cœurs :

| Grandeur | Valeur |
|---|---|
| Traitement | **1,1 à 1,2 s/document** |
| Téléchargement au SGG | ~1,2 à 1,8 s/document |
| Documents par page d'index | 16,5 à 18 |
| Sources | 1,2 à 1,7 Mo/document |
| Archive | ~25 Ko/document |
| Documents nécessitant un OCR | **~20 %** — mais ils consomment l'essentiel du temps |

**Ne demandez pas de GPU.** Le runtime `--gpu T4` donne les mêmes deux cœurs
sur un processeur plus ancien, et aucun outil de la chaîne — Tesseract,
Ghostscript, PyMuPDF, unpaper — n'a de version CUDA. La carte resterait
inutilisée. Elle deviendra utile le jour où les embeddings seront activés.

---

## Architecture

```text
                    ┌───────────────┐
                    │ Documents PDF │
                    └───────┬───────┘
                            ↓
                   ┌─────────────────┐
                   │ Document Loader │  core/loader.py
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ PDF Classifier  │  core/classifier.py
                   └──────┬─────┬────┘
                          ↓     ↓
                     Texte     Scan
                       ↓        ↓
                   PyMuPDF   OCRmyPDF        core/extraction/
                          \     /
                           ↓   ↓
                   ┌─────────────────┐
                   │ Text Normalizer │  core/cleaning/
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Legal Parser    │  core/parser/
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Metadata Engine │  core/metadata/
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Relations/Dedup │  core/relations.py, core/dedup.py
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Quality Checker │  core/validation/
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Structured Data │  core/storage/
                   └───────┬─────────┘
                           ↓
              ┌────────────┴────────────┐
              ↓                         ↓
        SQLite / JSONL             Embeddings      core/embeddings.py
                                        ↓
                                  Vector Database  core/vectorstore.py
```

### Organisation du code

```text
bldp/
├── cli.py              interface en ligne de commande
├── config.py           configuration YAML fusionnée
├── models.py           modèle de données (dataclasses)
├── pipeline.py         orchestration
├── utils.py            hachage, identifiants, numérotation juridique
├── core/               cœur générique, sans particularité nationale
│   ├── loader.py           module 1 — importation
│   ├── classifier.py       module 2 — analyse PDF, décision OCR
│   ├── extraction/         modules 3-4 — PyMuPDF, OCR
│   ├── cleaning/           module 5 — nettoyage
│   ├── parser/             modules 5-6 — structure et articles
│   ├── metadata/           module 7 — métadonnées
│   ├── relations.py        module 8 — statuts, versions, relations
│   ├── dedup.py            module 9 — doublons
│   ├── validation/         module 10 — qualité
│   ├── chunking.py         découpage juridiquement conscient
│   ├── embeddings.py       vecteurs (optionnel)
│   ├── vectorstore.py      index FAISS (optionnel)
│   └── storage/            SQLite, JSONL, JSON, CSV
├── jurisdictions/      règles nationales, séparées du cœur
│   ├── registry.py
│   ├── generic/
│   └── benin/
└── web/                interface locale (optionnelle)
```

**Le cœur ne connaît rien du Bénin.** Tout ce qui est national — formes
d'en-têtes, autorités, formules de promulgation, numérotation officielle — vit
dans `jurisdictions/benin/`.

---

## Formats supportés

### En entrée

| Format | État |
|--------|------|
| `.pdf` natif | pris en charge |
| `.pdf` scanné | pris en charge via OCR (extra `ocr`) |
| `.docx`, `.txt`, `.html` | prévus ultérieurement |

Le classement en sous-dossiers (`input/lois/`, `input/codes/`…) est **facultatif** :
il sert d'indice de catégorisation, rien de plus.

### Structures juridiques reconnues

`Partie`, `Livre`, `Titre`, `Sous-titre`, `Chapitre`, `Section`, `Sous-section`,
`Paragraphe`, `Article`, `Annexe`.

Formes d'articles reconnues : `Article 1`, `Article 1er`, `Article premier`,
`Art. 1`, `ARTICLE 1er`, `Article 45 bis`, `Article 45-2`, `Article unique`,
`Article 45 nouveau`.

---

## Formats de sortie

```text
data/exports/
├── documents.jsonl          un document par ligne
├── articles.jsonl           un article par ligne, avec son contexte complet
├── chunks.jsonl             fragments prêts pour l'indexation
├── metadata.json            métadonnées consolidées, confiances et preuves
├── quality_report.json      rapport qualité agrégé
├── articles.csv             export tableur (si demandé)
├── legal_database.sqlite    base structurée
├── pipeline_summary.json    bilan de la dernière exécution
└── run_report_*.json        journal horodaté de chaque exécution
```

Exemple d'article exporté :

```json
{
  "article_id": "code_travail_article_45",
  "document_id": "code_travail",
  "article_number": "45",
  "text": "Le salarié a droit à un repos hebdomadaire...",
  "alineas": [{ "index": 0, "text": "...", "number": null }],
  "title": "Titre II",
  "chapter": "Chapitre III",
  "section": "Section 2",
  "hierarchy_path": ["Titre II", "Chapitre III", "Section 2"],
  "page_start": 12,
  "page_end": 12,
  "source_file": "code_travail.pdf",
  "document_number": "2026-001",
  "document_date": "2026-02-10",
  "document_status": "en_vigueur",
  "validation": "en_attente"
}
```

Chaque article porte les métadonnées de son document : un moteur RAG peut citer
sa source sans jointure.

---

## Configuration

Aucun paramètre n'est codé en dur. Tout vit dans `config/default.yaml`.

Pour adapter sans modifier le dépôt, créez `config/local.yaml` avec seulement
les clés à changer — la fusion est récursive :

```yaml
ocr:
  language: fra+eng

parser:
  min_article_chars: 30

quality:
  minimum_score: 0.85

embeddings:
  enabled: true
```

Ou ponctuellement :

```bash
python -m bldp pipeline ./input --set ocr.enabled=false --set quality.minimum_score=0.8
```

Ordre de précédence : `default.yaml` → `local.yaml` → `--config` → `--set`.

---

## Exemples

### Savoir quels PDF nécessitent un OCR

```bash
$ python -m bldp analyze ./input
DOCUMENT                        PAGES   TEXTE        OCR   CONF.  ROUTE
------------------------------------------------------------------------
code_travail                      312     oui    inutile    0.95  native
    · 100% des pages contiennent du texte exploitable (2140 caractères/page)
decret_scanne                      14     non     requis    0.98  ocr
    · aucun texte natif : document très probablement scanné
    · 14 image(s) détectée(s)
```

### Vérifier la structure détectée

```bash
$ python -m bldp parse input/lois/loi_2026_001.pdf
Document   : loi_2026_001  (2 pages, route « native »)
Structure  : 4 subdivision(s)
Articles   : 4

TITRE PREMIER
  CHAPITRE I
  CHAPITRE II
    Section 1

[p.1] Article 1er : La presente loi fixe les regles applicables.
    contexte : TITRE PREMIER > CHAPITRE I
    alinéas  : 1   44 caractères
[p.2] Article 3 : Le contrat de travail est conclu librement.
    contexte : TITRE PREMIER > CHAPITRE II > Section 1
```

### Remonter à la source d'un article

```bash
$ python -m bldp trace code_travail_article_45
Article        : 45  (code_travail_article_45)
Document       : Code du travail
  contexte     : Titre II > Chapitre III > Section 2
  pages        : 12–12
  fichier      : /input/codes/code_travail.pdf
  validation   : valide
```

---

## Traçabilité et validation humaine

Le pipeline conserve la chaîne complète :

```text
document original → page → texte brut → texte nettoyé → article → métadonnées → version
```

Concrètement :

- chaque page stocke **à la fois** son texte brut et son texte nettoyé : le
  nettoyage reste contestable et rejouable ;
- chaque métadonnée déduite porte un **score de confiance** et sa **preuve** ;
- chaque décision d'OCR est **motivée** ;
- `bldp trace <article_id>` remonte d'un article à sa page et à son PDF.

### Le système ne se valide jamais lui-même

`bldp validate --document <id>` affiche les trois couches côte à côte :

```text
① Page d'origine (texte brut extrait)
② Page après nettoyage
③ Article structuré
```

Trois décisions humaines sont possibles : `valide`, `a_verifier`, `rejete`. Un
document dont le contrôle qualité ne trouve rien reste `en_attente` — jamais
`valide` automatiquement. La décision d'un relecteur **survit** à un
retraitement.

---

## Relecture assistée par un modèle

`bldp relire` ne demande pas à un modèle de *corriger* le texte. Il lui demande
de le **collationner** : mettre l'image de la page d'origine en regard de ce
que la chaîne en a tiré, et rapporter ce qui diffère. Ce qu'il doit écrire,
c'est ce qui figure sur l'image — jamais ce qui devrait logiquement s'y
trouver.

### Pourquoi l'image, et pas seulement le texte

C'est le point à comprendre avant tout le reste. Sans image, un modèle qui
« relit » compare le texte des articles au texte des pages — or les deux
sortent de la **même** extraction. Là où l'OCR s'est trompé, il s'est trompé
aux deux endroits : il n'y a rien à comparer, et tout ce que le modèle
proposerait serait déduit de ce qui *paraît* cohérent.

Un exemple réel du corpus. L'OCR de `arrete_2018_001` produit :

```text
Articl'6 promi�r
F-rr appircalion des drsposilrons des artr�les 3 et 4 du ci�cret r"r" 2018-106
```

Là où l'image porte, nettement :

```text
Article premier
En application des dispositions des articles 3 et 4 du décret n° 2018-106
```

Aucune quantité de raisonnement sur le seul texte ne permet de retrouver
« Article premier » de façon sûre. Avec l'image, il suffit de lire. L'image de
la page est la seule référence qui ne vienne pas de l'OCR — c'est pour cela
qu'un document dont le PDF d'origine a disparu est **écarté** plutôt que relu.

### Le modèle propose, la mécanique dispose

Aucune correction n'est appliquée sur la seule parole du modèle. Comme la
référence — l'image — n'est pas mécaniquement lisible, on ne peut pas *prouver*
qu'une lecture est juste ; on peut en revanche **borner** ce qu'une lecture a
le droit d'être :

| Contrôle | Laisse passer | Arrête |
|---|---|---|
| l'image a bien été transmise | une lecture sur une page réellement envoyée | une correction qui invoque une image jamais reçue |
| la page citée porte l'article | « je lis ceci page 3 », l'article étant page 3 | une page qui n'existe pas, ou qui ne porte pas l'article |
| le texte reste le même texte | une transcription, même d'un scan très abîmé | une reformulation, une invention |
| la longueur ne dérive pas | une ligne réparée | une phrase ajoutée |
| un numéro s'inscrit dans la suite | « Article I » → « 8 » entre le 7 et le 9 | « Article I » → « 42 » |
| rien ne disparaît | — | vider un article, en supprimer un |
| ce n'est pas une réécriture | quelques articles réparés | la majorité du document corrigée |

Le troisième contrôle a été **calibré sur le corpus**, pas choisi au jugé. Sur
un scan très dégradé, une transcription pourtant exacte n'atteint que 0,56 de
fidélité, quand une invention plausible tombe à 0,40 et une reformulation à
0,35. Un seuil réglé sur des dégâts légers aurait rejeté précisément les
documents qui ont le plus besoin d'être relus. Les mesures sont figées dans
`tests/test_review.py::TestCalibrageDeLaFidelite`.

Ce qui ne passe pas n'est pas jeté : c'est retenu comme **signalement**, avec
la lecture proposée, et un humain tranche. Rétablir une ligne entière que
l'OCR avait manquée et inventer une ligne entière sont d'ailleurs
mécaniquement indiscernables — les deux sont donc refusées et signalées, ce
qui est la bonne réponse : cette décision-là revient à une personne.

Enfin, les fautes du document d'origine sont **conservées**. Si un décret
imprime « un (02) socio-anthropologues », cette incohérence *est* le texte
officiel.

### Rien ne part sans consentement explicite

Relire avec un modèle distant envoie le texte des documents hors de la machine,
ce que le §27 interdit par défaut. Quatre verrous doivent être levés :

```yaml
# config/local.yaml
privacy:
  allow_external_calls: true    # 1. autoriser toute sortie de document
ai_review:
  enabled: true                 # 2. autoriser la relecture
```

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # 3. la clé, jamais dans un fichier
pip install -e ".[review]"
```

```bash
# 4. sans --oui, la commande n'appelle rien : elle annonce
python -m bldp relire --etape a_verifier --limit 10
```

```text
Mode : collation — l'image de chaque page part avec le texte, et fait foi.
DOCUMENT                    PAGES  ART.   CARACT.  IMAGES  ÉTAT
accord_2022_324                17    55     25842      17  à relire
arrete_2018_001                 2     2      3044       2  à relire
11 document(s) partiraient hors de cette machine, dont 47 image(s) de page ; 0 écarté(s).
    Une image de page comporte aussi les en-têtes, tampons et signatures de l'original.
Coût estimé : 1.42 $ (plafond 5.13 $ si toutes les réponses sont maximales).

Rien n'a été envoyé. Pour lancer réellement la relecture, ajoutez --oui.
```

Notez l'avertissement : la collation envoie l'**image** des pages, qui porte
davantage que leur texte — en-têtes, tampons, signatures manuscrites. C'est
dit avant l'envoi, pas après.

Un document trop long, ou dont le PDF d'origine a disparu, est **écarté plutôt
que relu à moitié** : relire une partie d'un texte et conclure sur l'ensemble
serait un faux diagnostic.

### La relecture ne valide pas

Un document relu passe à l'étape `revue_ia`, badge `[IA]` — jamais à `valide`.
Un modèle qui se tromperait sur un article de loi le ferait de façon plausible,
donc invisible ; la signature reste humaine (§16). Un verdict `douteux` renvoie
au contraire le ticket à `a_verifier`.

`ai_review.can_validate: true` lève cette réserve. À n'activer qu'en sachant
exactement ce qu'on échange contre le temps gagné.

```bash
python -m bldp relire --etape a_verifier --oui --rapport data/relecture.json
python -m bldp suivi liste --etape revue_ia
```

L'interface web montre l'état de la relecture et le coût d'un lot, mais **ne
peut pas la lancer** : un clic n'est pas un consentement éclairé.

---

## Limites connues

Ce projet est un MVP. Il faut en connaître les limites avant de s'appuyer sur
ses sorties.

**Extraction**
- La qualité de l'OCR conditionne tout le reste. Un scan médiocre produit un
  texte médiocre — le contrôle qualité le signale, mais ne le répare pas.
- Les PDF en colonnes multiples ou à mise en page complexe (tableaux) peuvent
  produire un ordre de lecture incorrect.
- Les tableaux ne sont pas structurés : leur contenu est extrait comme du texte.

**Parsing**
- Les règles sont conçues pour le français juridique usuel. Un document au
  format inhabituel peut ne produire aucun article — c'est signalé, pas masqué.
- Un article n'est délimité que par l'en-tête suivant : une numérotation absente
  ou illisible peut fusionner deux articles. Le contrôle qualité le repère par
  la longueur anormale.

**Métadonnées et relations**
- La détection des relations est **semi-automatique**, comme prévu au cahier des
  charges. Une clause vague (« sont abrogées toutes dispositions antérieures
  contraires ») ne modifie aucun statut.
- Un statut juridique déduit d'une relation porte toujours la mention « à
  confirmer par un juriste ». **Ne vous y fiez pas sans vérification.**
- Le domaine juridique est déduit par mots-clés : purement indicatif.

**Portée**
- BLDP ne fait **aucune analyse juridique**. Il structure du texte, il ne
  l'interprète pas.
- Un score de qualité élevé signifie « rien de suspect détecté », pas « exact ».

---

## Autres juridictions

Le cœur étant générique, ajouter un pays consiste à créer un paquet :

```bash
mkdir -p bldp/jurisdictions/togo
cp bldp/jurisdictions/benin/rules.py bldp/jurisdictions/togo/rules.py
touch bldp/jurisdictions/togo/__init__.py
```

Adaptez ensuite les motifs (`DOCUMENT_TYPE_PATTERNS`, `AUTHORITY_PATTERNS`,
`NUMBER_PATTERNS`, formules de promulgation…), puis :

```yaml
# config/local.yaml
project:
  jurisdiction: togo
```

Aucune modification de `bldp/core/` n'est nécessaire. Une juridiction inconnue
retombe sur les règles génériques avec un avertissement, plutôt que de bloquer.

---

## Tests

```bash
pip install -e ".[dev]"
python -m pytest
python -m pytest --cov=bldp --cov-report=term-missing
```

867 tests — 865 passants, 2 sautes faute de binaires OCR — couvrent notamment :

- extraction native et OCR (mocké — les binaires ne sont pas requis) ;
- **préservation du contenu juridique** au nettoyage (articles, alinéas,
  références, dates, montants, sanctions, exceptions) ;
- les quatre formes d'articles imposées, plus `bis`, `ter`, `unique`, `nouveau` ;
- détection des ruptures de numérotation (`1, 3` → anomalie) ;
- relations `modifie` / `abroge` / `remplace` et propagation des statuts ;
- isolation des erreurs : un document corrompu n'arrête pas le lot ;
- traçabilité article → page → fichier source ;
- les 15 critères de réussite du §31 du cahier des charges.

Les tests nécessitant l'OCR ou les embeddings portent les marqueurs
`requires_ocr` / `requires_embeddings` et sont ignorés automatiquement.

---

## Contribuer

Les contributions sont bienvenues — voir [CONTRIBUTING.md](CONTRIBUTING.md) et
le [code de conduite](CODE_OF_CONDUCT.md).

Une règle prime sur toutes les autres : **toute modification qui pourrait
altérer un contenu juridique doit être accompagnée d'un test qui prouve le
contraire.**

---

## Licence

Apache-2.0 — voir [LICENSE](LICENSE).

Cette licence couvre le **code**. Les documents juridiques que vous traitez
relèvent de leur propre régime : au Bénin comme ailleurs, vérifiez les
conditions de réutilisation des sources officielles avant toute rediffusion.
