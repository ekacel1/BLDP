# Benin Legal Data Pipeline (BLDP)

Transforme des documents juridiques bruts du Bénin (PDF natifs ou scannés) en un
**corpus juridique propre, structuré et auditable**, exploitable par un futur
système de recherche/RAG.

> **Principe fondamental : exactitude > automatisation.**
> En cas de doute, le pipeline ne devine pas. Il signale, et demande une
> validation humaine.

[![Tests](https://img.shields.io/badge/tests-531%20passants-brightgreen)](#tests)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#installation)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-lightgrey)](LICENSE)

---

## Sommaire

- [Ce que fait BLDP](#ce-que-fait-bldp)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Architecture](#architecture)
- [Formats supportés](#formats-supportés)
- [Formats de sortie](#formats-de-sortie)
- [Configuration](#configuration)
- [Exemples](#exemples)
- [Traçabilité et validation humaine](#traçabilité-et-validation-humaine)
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

531 tests couvrent notamment :

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
