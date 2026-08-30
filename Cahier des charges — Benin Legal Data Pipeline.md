# Cahier des charges — Benin Legal Data Pipeline

## 1. Présentation du projet

### Nom provisoire

**Benin Legal Data Pipeline (BLDP)**

### Objectif

Développer un outil open source permettant de transformer automatiquement des documents juridiques bruts du Bénin (PDF natifs, PDF scannés, documents OCRisés, etc.) en un **corpus juridique propre, structuré, exploitable par des systèmes d'IA/RAG**.

L'outil doit notamment :

1. importer des documents juridiques ;
2. déterminer automatiquement s'ils contiennent du texte exploitable ou s'ils nécessitent un OCR ;
3. extraire le texte ;
4. nettoyer le texte sans altérer son contenu juridique ;
5. détecter les structures juridiques (articles, chapitres, titres, sections, etc.) ;
6. extraire les métadonnées ;
7. détecter les éventuels doublons ;
8. permettre d'identifier les modifications, abrogations et différentes versions d'un texte ;
9. effectuer des contrôles de qualité ;
10. produire un corpus structuré en JSON/JSONL/CSV/SQLite ;
11. générer les données nécessaires à une future indexation vectorielle ;
12. être facilement réutilisable pour d'autres pays d'Afrique francophone.

---

# 2. Vision à long terme

Le projet est la première brique d'un futur système :

```text
Documents juridiques officiels
             ↓
       BLDP Pipeline
             ↓
     Corpus juridique propre
             ↓
       Recherche / RAG
             ↓
       Modèle de langage
             ↓
   Assistant juridique béninois
```

Le pipeline doit donc être conçu dès le départ pour ne pas être dépendant d'un seul modèle d'IA.

---

# 3. Périmètre du MVP

Le MVP doit fonctionner **localement sur un ordinateur personnel**, sans service payant obligatoire.

### Contraintes matérielles de développement

Configuration cible minimale :

- RAM : 16 Go ;
- stockage disponible : environ 40–50 Go ;
- CPU classique ;
- GPU non obligatoire.

Le système doit pouvoir traiter les documents sans nécessiter de GPU.

---

# 4. Stack technique recommandée

### Langage

**Python 3.11+**

### Extraction PDF

Utiliser :

- PyMuPDF (`fitz`) en priorité.

### OCR

Utiliser :

- OCRmyPDF ;
- Tesseract OCR.

Le système doit privilégier l'extraction native lorsque le PDF contient déjà du texte et utiliser l'OCR uniquement lorsque nécessaire.

### Nettoyage

- Python ;
- expressions régulières ;
- règles configurables.

### Parsing juridique

Développer un parser spécialisé capable de détecter notamment :

```text
Titre
Sous-titre
Livre
Partie
Chapitre
Section
Sous-section
Article
Paragraphe
Alinéa
Annexe
```

Le parser doit être conçu de manière modulaire afin que les règles puissent être adaptées à différents formats de documents.

### Embeddings

Prévoir une intégration avec :

**Sentence Transformers**

L'utilisation des embeddings doit être optionnelle dans le pipeline.

### Base vectorielle

Prévoir une compatibilité avec :

- FAISS ;
- éventuellement Qdrant dans une version ultérieure.

### Base structurée

Pour le MVP :

**SQLite**

Les exports JSONL doivent également être disponibles.

---

# 5. Architecture générale

Architecture recommandée :

```text
                    ┌───────────────┐
                    │ Documents PDF │
                    └───────┬───────┘
                            ↓
                   ┌─────────────────┐
                   │ Document Loader │
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ PDF Classifier  │
                   └──────┬─────┬────┘
                          ↓     ↓
                     Texte     Scan
                       ↓        ↓
                   PyMuPDF   OCRmyPDF
                          \     /
                           \   /
                            ↓ ↓
                   ┌─────────────────┐
                   │ Text Normalizer │
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Legal Parser    │
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Metadata Engine │
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Quality Checker │
                   └────────┬────────┘
                            ↓
                   ┌─────────────────┐
                   │ Structured Data │
                   └───────┬─────────┘
                           ↓
              ┌────────────┴────────────┐
              ↓                         ↓
        SQLite / JSONL             Embeddings
                                        ↓
                                  Vector Database
```

---

# 6. Module 1 — Importation des documents

Le système doit accepter au minimum :

- `.pdf`

Prévoir ultérieurement :

- `.docx`
- `.txt`
- `.html`

Structure :

```text
input/
├── lois/
├── codes/
├── decrets/
├── arretes/
├── jurisprudence/
└── autres/
```

Le classement manuel ne doit cependant pas être obligatoire.

---

# 7. Module 2 — Analyse du PDF

Pour chaque PDF, déterminer :

- nombre de pages ;
- taille ;
- présence de texte ;
- quantité de texte par page ;
- présence éventuelle d'images ;
- présence probable d'un scan.

Exemple :

```json
{
  "pages": 150,
  "has_text": true,
  "ocr_required": false,
  "confidence": 0.98
}
```

Si le document contient suffisamment de texte exploitable :

```text
PDF → PyMuPDF
```

Sinon :

```text
PDF → OCRmyPDF/Tesseract → texte
```

---

# 8. Module 3 — Extraction

Extraire le texte page par page.

Conserver les informations de provenance :

```json
{
  "page": 12,
  "text": "...",
  "source_file": "code_travail.pdf"
}
```

Il est impératif de conserver le numéro de page afin de pouvoir retrouver l'origine exacte d'une information.

---

# 9. Module 4 — Nettoyage

Le nettoyage doit supprimer les artefacts techniques **sans modifier le contenu juridique**.

### Supprimer ou corriger

- espaces multiples ;
- retours à la ligne artificiels ;
- caractères OCR manifestement erronés ;
- en-têtes répétitifs ;
- pieds de page répétitifs ;
- numéros de page isolés ;
- caractères de contrôle ;
- doublons techniques.

### Ne jamais supprimer automatiquement

- articles ;
- paragraphes ;
- alinéas ;
- références légales ;
- dates ;
- montants ;
- numéros ;
- sanctions ;
- exceptions ;
- conditions juridiques.

Le système doit privilégier la conservation du texte original lorsqu'il existe un doute.

---

# 10. Module 5 — Détection des structures juridiques

Le parser doit identifier automatiquement les éléments suivants :

```text
TITRE
CHAPITRE
SECTION
SOUS-SECTION
ARTICLE
PARAGRAPHE
ALINÉA
ANNEXE
```

Exemple :

```text
Titre II
    Chapitre III
        Section 2
            Article 45
```

Chaque article doit conserver son contexte hiérarchique.

---

# 11. Module 6 — Extraction des articles

Chaque article doit devenir une unité exploitable.

Exemple :

```json
{
  "article_id": "code_travail_article_45",
  "article_number": "45",
  "text": "....",
  "document_id": "code_travail",
  "title": "Titre II",
  "chapter": "Chapitre III",
  "section": "Section 2"
}
```

Si un article contient plusieurs alinéas, ils doivent être conservés dans l'ordre.

---

# 12. Module 7 — Métadonnées

Chaque document doit posséder des métadonnées.

### Métadonnées minimales

```text
document_id
titre
type_document
numero
date
autorite
domaine_juridique
source
source_url
date_recuperation
langue
version
statut
```

### Exemple

```json
{
  "document_id": "loi_2026_001",
  "title": "...",
  "type": "loi",
  "number": "2026-001",
  "date": "2026-02-10",
  "jurisdiction": "Benin",
  "language": "fr",
  "source": "SGG",
  "source_url": "...",
  "retrieved_at": "2026-08-30",
  "status": "en_vigueur"
}
```

---

# 13. Module 8 — Statut juridique et versions

Le système doit prévoir les statuts :

```text
en_vigueur
modifie
abroge
partiellement_abroge
remplace
inconnu
```

Prévoir des relations entre textes :

```text
Loi A
   ↓
modifiée par
   ↓
Loi B
```

et :

```text
Loi A
   ↓
abrogée par
   ↓
Loi B
```

Pour le MVP, la détection peut être semi-automatique.

Le système doit signaler les relations qu'il n'est pas suffisamment sûr de déterminer.

---

# 14. Module 9 — Détection des doublons

Détecter :

- fichiers identiques ;
- documents identiques avec noms différents ;
- versions identiques ;
- doublons partiels.

Utiliser notamment :

- hash de fichier ;
- hash du texte ;
- éventuellement similarité textuelle.

Le système doit conserver une trace des doublons plutôt que les supprimer définitivement.

---

# 15. Module 10 — Contrôle qualité

Chaque document doit recevoir un rapport de qualité.

Exemple :

```json
{
  "document_id": "...",
  "ocr_quality": 0.94,
  "text_quality": 0.98,
  "articles_detected": 142,
  "possible_errors": 3,
  "missing_pages": 0,
  "duplicate_pages": 0,
  "status": "review_required"
}
```

Détecter notamment :

- pages sans texte ;
- caractères anormaux ;
- articles manquants ;
- numérotation incohérente ;
- texte OCR suspect ;
- pages dupliquées ;
- documents incomplets.

---

# 16. Validation humaine

Le système ne doit jamais prétendre que son extraction est parfaite.

Prévoir une interface ou au minimum un rapport permettant de vérifier :

```text
Document original
        ↕
Texte extrait
        ↕
Article structuré
```

Le développeur doit pouvoir sélectionner :

```text
VALIDÉ
À VÉRIFIER
REJETÉ
```

---

# 17. Export des données

Le pipeline doit produire au minimum :

### JSONL

```text
documents.jsonl
articles.jsonl
```

### SQLite

```text
legal_database.sqlite
```

### JSON

```text
metadata.json
quality_report.json
```

Prévoir ultérieurement :

- CSV ;
- Parquet.

---

# 18. Format des données

Structure recommandée :

```text
data/
├── raw/
├── processed/
├── validated/
├── embeddings/
└── exports/
```

Ne jamais modifier les documents originaux.

---

# 19. Embeddings

Créer un module indépendant :

```text
structured data
      ↓
chunking
      ↓
embedding model
      ↓
vectors
```

Les embeddings doivent conserver les métadonnées originales.

Exemple :

```json
{
  "vector_id": "...",
  "document_id": "...",
  "article_id": "...",
  "article_number": "45",
  "text": "...",
  "embedding_model": "..."
}
```

---

# 20. Chunking

Le découpage doit être juridiquement conscient.

Priorité :

```text
Article
 ↓
alinéas
 ↓
paragraphes
```

Éviter de couper arbitrairement une phrase juridique au milieu.

Chaque chunk doit conserver :

- document ;
- article ;
- chapitre ;
- section ;
- page source ;
- position dans le document.

---

# 21. CLI

Le MVP doit pouvoir être utilisé depuis le terminal.

Exemples :

```bash
python -m bldp ingest ./input
```

```bash
python -m bldp process ./input
```

```bash
python -m bldp validate ./data/processed
```

```bash
python -m bldp embed ./data/validated
```

```bash
python -m bldp export
```

Une commande globale peut également être prévue :

```bash
python -m bldp pipeline ./input
```

---

# 22. Interface utilisateur

Pour le MVP, une interface web simple est souhaitable mais non prioritaire.

Elle pourrait permettre :

- upload d'un PDF ;
- lancement du traitement ;
- affichage de la progression ;
- aperçu du texte ;
- aperçu des articles ;
- affichage des erreurs ;
- validation manuelle ;
- téléchargement du résultat.

Technologie possible :

**FastAPI + interface web simple**

Ne pas construire une interface complexe au début.

---

# 23. Configuration

Les paramètres doivent être configurables dans un fichier :

```yaml
ocr:
  enabled: true
  language: fra

parser:
  detect_articles: true

embeddings:
  enabled: false

quality:
  minimum_score: 0.90
```

Éviter de coder en dur les paramètres.

---

# 24. Tests

Le projet doit posséder une suite de tests.

Tester au minimum :

### PDF texte

```text
PDF → extraction → nettoyage → articles
```

### PDF scanné

```text
PDF → OCR → nettoyage → articles
```

### Documents avec en-têtes

Vérifier leur suppression.

### Articles

Tester :

```text
Article 1
Article 1er
Article premier
Art. 1
```

### Numérotation

Tester :

```text
Article 1
Article 2
Article 3
```

et détecter :

```text
Article 1
Article 3
```

comme anomalie potentielle.

### Documents modifiés

Tester les relations :

```text
modifie
abroge
remplace
```

---

# 25. Journalisation

Chaque traitement doit générer un log.

Exemple :

```text
[INFO] Document chargé
[INFO] 120 pages détectées
[INFO] Texte détecté
[INFO] OCR non nécessaire
[INFO] 185 articles détectés
[WARNING] Article 94 potentiellement incomplet
[INFO] Validation terminée
```

---

# 26. Gestion des erreurs

Une erreur sur un document ne doit pas arrêter tout le pipeline.

Exemple :

```text
100 documents
       ↓
96 réussis
2 nécessitent une vérification
2 échoués
```

Le pipeline doit continuer et générer un rapport.

---

# 27. Confidentialité et sécurité

Les documents doivent être traités localement par défaut.

Aucun document ne doit être envoyé automatiquement à une API externe.

Les appels à des services externes devront être :

- optionnels ;
- clairement configurables ;
- désactivés par défaut.

---

# 28. Open source

Le projet doit être conçu pour être publié sur GitHub.

Prévoir :

```text
README.md
LICENSE
CONTRIBUTING.md
CODE_OF_CONDUCT.md
CHANGELOG.md
```

Le README doit expliquer :

- installation ;
- utilisation ;
- architecture ;
- formats supportés ;
- exemples ;
- limites ;
- contribution.

Le code doit être documenté.

---

# 29. Réutilisation internationale

Même si le premier corpus concerne le Bénin, le code doit être conçu pour pouvoir intégrer d'autres juridictions.

Architecture :

```text
core/
    extraction/
    cleaning/
    parser/
    metadata/
    validation/

jurisdictions/
    benin/
    togo/
    cote_ivoire/
    senegal/
```

Les règles spécifiques au Bénin doivent être séparées du cœur générique.

---

# 30. Ce qui n'est PAS demandé dans le MVP

Ne pas développer pour l'instant :

- chatbot juridique ;
- application mobile ;
- entraînement d'un LLM ;
- fine-tuning ;
- paiement ;
- système utilisateur complexe ;
- authentification avancée ;
- cloud obligatoire ;
- analyse juridique automatique des situations ;
- prédiction des décisions judiciaires.

Ces fonctionnalités appartiendront à la phase suivante.

---

# 31. Critères de réussite du MVP

Le MVP sera considéré comme fonctionnel lorsqu'il pourra :

1. recevoir un dossier contenant plusieurs PDF ;
2. identifier automatiquement les PDF nécessitant un OCR ;
3. extraire leur contenu ;
4. nettoyer les artefacts courants ;
5. identifier les articles ;
6. conserver leur hiérarchie juridique ;
7. générer les métadonnées ;
8. détecter les anomalies ;
9. générer un rapport de qualité ;
10. exporter les données en JSONL ;
11. enregistrer les données dans SQLite ;
12. générer optionnellement les embeddings ;
13. permettre de retrouver l'article original et sa page source ;
14. fonctionner entièrement localement ;
15. fonctionner sans API payante.

---

# 32. Ordre de développement recommandé

L'agent IA doit développer dans cet ordre :

### Phase 1

Initialisation du projet + architecture.

### Phase 2

PDF → texte avec PyMuPDF.

### Phase 3

Détection des PDF nécessitant OCR.

### Phase 4

OCRmyPDF/Tesseract.

### Phase 5

Nettoyage.

### Phase 6

Parser juridique.

### Phase 7

Métadonnées.

### Phase 8

Base SQLite + JSONL.

### Phase 9

Validation et quality scoring.

### Phase 10

Gestion des versions et relations juridiques.

### Phase 11

Embeddings.

### Phase 12

FAISS.

### Phase 13

CLI complète.

### Phase 14

Interface web minimale.

### Phase 15

Tests, documentation et préparation GitHub.

---

# 33. Principe fondamental

Le système doit toujours privilégier :

**Exactitude > automatisation**

En cas de doute :

```text
NE PAS DEVINER
      ↓
SIGNALER
      ↓
VALIDATION HUMAINE
```

Le système est destiné à constituer une base de données juridique utilisée ultérieurement par une IA. Une erreur d'extraction pourrait entraîner une mauvaise interprétation juridique.

Le pipeline doit donc conserver autant que possible :

**document original → page → texte → article → métadonnées → version**

afin que toute information puisse être auditée et retrouvée dans sa source.

---

# 34. Livrable final du MVP

Le projet doit fournir :

```text
Benin Legal Data Pipeline
│
├── Code source
├── CLI
├── Documentation
├── Tests
├── Configuration
├── Pipeline PDF/OCR
├── Parser juridique
├── Base SQLite
├── Export JSONL
├── Module embeddings
├── Module FAISS
└── Rapport qualité
```

Le résultat final doit permettre de prendre un ensemble de documents juridiques bruts et de produire un **corpus juridique structuré, vérifiable et directement exploitable par un futur système RAG juridique**.