# Feuille de route du MVP

Ordre de développement imposé par le §32 du cahier des charges.

| Phase | Contenu | État |
|------:|---------|------|
| 1  | Initialisation du projet + architecture        | ✅ terminé |
| 2  | PDF → texte avec PyMuPDF                        | ✅ terminé |
| 3  | Détection des PDF nécessitant un OCR            | ✅ terminé |
| 4  | OCRmyPDF / Tesseract                            | ✅ terminé |
| 5  | Nettoyage                                       | ✅ terminé |
| 6  | Parser juridique                                | ✅ terminé |
| 7  | Métadonnées                                     | ✅ terminé |
| 8  | Base SQLite + JSONL                             | ✅ terminé |
| 9  | Validation et quality scoring                   | ✅ terminé |
| 10 | Gestion des versions et relations juridiques    | ✅ terminé |
| 11 | Embeddings                                      | ✅ terminé |
| 12 | FAISS                                           | ✅ terminé |
| 13 | CLI complète                                    | ✅ terminé |
| 14 | Interface web minimale                          | ✅ terminé |
| 15 | Tests, documentation, préparation GitHub        | ✅ terminé |

## Phase 1 — livré

- Arborescence `bldp/core/*` + `bldp/jurisdictions/*` (§29 : le coeur générique
  est séparé des règles propres au Bénin).
- `bldp/config.py` : configuration YAML fusionnée (défaut → local → `--config`
  → `--set`), aucun paramètre codé en dur (§23).
- `bldp/models.py` : modèle de données complet portant la chaîne de traçabilité
  `document → page → texte → article → métadonnées → version` (§33).
- `bldp/logging_setup.py` : journalisation console + fichier (§25).
- `bldp/utils.py` : hachage, identifiants stables, numérotation juridique
  (`1er`, `premier`, `XII`, `45 bis`).
- CLI `python -m bldp` avec `config` et `doctor`.
- 41 tests unitaires.

## Phase 2 — livré

- `bldp/core/loader.py` (module 1) : découverte récursive, empreinte SHA-256,
  identifiants stables, catégorie déduite du sous-dossier (classement manuel
  facultatif), copie de travail dans `data/raw/` — l'original n'est jamais
  modifié.
- `bldp/core/extraction/pymupdf_extractor.py` (module 3) : extraction page par
  page conservant `page` + `source_file`, `raw_text` figé pour l'audit, pages
  vides signalées et non supprimées, erreurs PDF explicites (fichier absent,
  corrompu, protégé par mot de passe).
- Commandes `python -m bldp ingest` et `python -m bldp extract`.
- Fixtures PDF générées à la volée (natif, scanné, vide, en-têtes répétés) :
  aucun document réel n'est versionné.

## Phase 3 — livré

- `bldp/core/classifier.py` (module 2) : mesure par page (caractères, images,
  ratio alphabétique, géométrie) puis décision motivée `ocr_required` +
  `confidence` + `reasons`.
- Trois déclencheurs d'OCR : aucun texte natif, trop peu de pages textuelles,
  texte présent mais non alphabétique (police cassée ou OCR antérieur dégradé).
- La confiance s'effondre au voisinage des seuils : les cas limites sont
  explicitement marqués « vérification humaine recommandée » (§33).
- Routage `native` / `ocr` / `hybrid` (OCR ciblé sur quelques pages faibles) ;
  OCR désactivé en configuration → repli natif signalé, jamais d'échec.
- Échantillonnage des très gros documents (contrainte 16 Go de RAM).
- Commande `python -m bldp analyze`.

## Phase 4 — livré

- `bldp/core/extraction/ocr.py` (module 4) : deux moteurs locaux avec repli
  automatique de l'un sur l'autre.
  - `ocrmypdf` : produit un PDF à couche texte conservé dans
    `data/processed/ocr/`, consultable côte à côte avec l'original pour la
    validation humaine (§16).
  - `tesseract` : OCR page par page, seule voie possible pour l'OCR **ciblé**
    de la route `hybrid`, avec récupération de la confiance par page (TSV).
- `check_ocr_ready()` : diagnostic explicite (moteur absent, langue non
  installée, OCR désactivé) remonté par `bldp doctor`.
- Robustesse §26 : OCR indisponible ou en échec → repli sur le texte natif,
  avertissement explicite, jamais d'arrêt du pipeline ni de corpus
  silencieusement amputé.
- Route `hybrid` : une page n'est remplacée par sa version OCR que si celle-ci
  apporte réellement plus de texte — en cas de doute, le natif est conservé (§9).
- Aucun appel réseau : les deux moteurs tournent localement (§27).

## Phase 5 — livré

- `bldp/core/cleaning/normalizer.py` (module 5). Deux garde-fous gouvernent tout
  le module : rien n'est supprimé sans preuve de répétition, et en cas de doute
  on conserve.
- **Veto de protection** : 12 familles de motifs (articles, alinéas, références
  légales, dates, montants, sanctions, exceptions, conditions) rendent une ligne
  intouchable par toute règle de suppression — c'est la liste « ne jamais
  supprimer » du §9, appliquée mécaniquement.
- Artefacts traités : caractères de contrôle, espaces insécables et largeur
  nulle, césures de fin de ligne, retours à la ligne artificiels, lignes
  décoratives, numéros de page isolés, en-têtes et pieds de page répétés.
- Les en-têtes ne sont détectés qu'au-delà de 3 pages et d'un ratio de
  répétition configurable ; les zones haute et basse ne se recouvrent jamais,
  sans quoi le corps d'une page courte pourrait être pris pour un en-tête.
- Corrections OCR appliquées **uniquement** aux pages réellement OCRisées, et
  limitées à des contextes non ambigus (`Artic1e` → `Article`, `2O26` → `2026`).
- `CleaningReport` journalise chaque suppression ; au-delà de 25 % de texte
  retiré, le document est marqué pour vérification humaine.
- `Page.raw_text` conserve le texte d'origine : la comparaison avant/après reste
  toujours possible (§16).
- Tests dédiés, dont une section entière consacrée à prouver que le contenu
  juridique survit au nettoyage.

## Phase 6 — livré

- `bldp/core/parser/rules.py` : vocabulaire de règles (`StructureRule`,
  `RuleSet`) piloté par priorité, plus le socle générique du français
  juridique. Compilable depuis le YAML (`compile_rules`) pour ajouter une forme
  locale sans écrire de Python.
- `bldp/core/parser/legal_parser.py` (modules 5 et 6) : linéarisation avec
  traçabilité des pages, détection des en-têtes, construction de l'arbre
  hiérarchique par pile, extraction des articles et découpage en alinéas.
- Les 10 niveaux du §10 sont couverts : partie, livre, titre, sous-titre,
  chapitre, section, sous-section, paragraphe, article, annexe.
- Formes d'articles du §24 : `Article 1`, `Article 1er`, `Article premier`,
  `Art. 1`, plus `45 bis`, `45-2`, `Article unique` et `Article 45 nouveau`
  (Bénin).
- `check_numbering()` signale les ruptures (`1, 3` → « article 2 manquant »)
  **sans jamais corriger** : corriger reviendrait à inventer du droit.
- Bruit écarté : sommaires (qui produiraient des articles fantômes) et blocs de
  signature ; les annexes restent reconnues au-delà de la promulgation.
- Rien n'est jeté : le texte antérieur au premier article devient le préambule.
- `bldp/jurisdictions/registry.py` + `benin/rules.py` : le cœur ignore tout du
  Bénin, les règles nationales sont fusionnées par-dessus le socle générique.
  Une juridiction inconnue retombe sur le générique avec avertissement plutôt
  que de bloquer le pipeline.
- Commande `python -m bldp parse`.

## Phase 7 — livré

- `bldp/core/metadata/engine.py` (module 7) : trois sources par fiabilité
  décroissante — fichier de métadonnées manuel (`<doc>.meta.yaml|json`), texte
  des premières pages, puis conteneur PDF et nom de fichier.
- Chaque champ deviné porte un **score de confiance** et sa **preuve**
  (`evidence`) : le pipeline peut toujours répondre à « d'où vient cette date ? ».
- Rien n'est inventé : une valeur introuvable reste vide et produit un
  avertissement « saisie manuelle recommandée ». Les dates impossibles
  (32 janvier, an 999) sont rejetées plutôt que corrigées.
- `status` vaut `inconnu` par défaut, jamais `en_vigueur` : supposer qu'un texte
  est en vigueur serait une erreur juridique (§13).
- La saisie manuelle écrase toujours la détection et reçoit une confiance de
  1.0 — c'est le mécanisme de correction du §16. Un fichier de métadonnées
  illisible est signalé sans faire échouer le document.
- `metadata_completeness()` et `iter_missing_fields()` alimenteront le contrôle
  qualité et l'interface de validation.

## Phase 8 — livré

- `bldp/core/storage/sqlite_store.py` : schéma en 11 tables matérialisant la
  chaîne `documents → pages → structure → articles → alineas → relations →
  duplicates → quality → chunks → embeddings`, avec cascades et index.
- `trace_article()` remonte en une requête `article → document → page → fichier
  d'origine` : c'est l'exigence d'auditabilité du §33 rendue exécutable, exposée
  par `python -m bldp trace <article_id>`.
- Les pages conservent **texte brut et texte nettoyé** : le nettoyage reste
  contestable et rejouable.
- Réécriture atomique et idempotente (pas d'accumulation de lignes filles) ;
  une décision de validation humaine **survit** à un retraitement (§16).
- `save_documents()` isole les échecs : un document en erreur n'empêche pas
  l'enregistrement des autres (§26).
- `bldp/core/storage/exporters.py` : `documents.jsonl`, `articles.jsonl`,
  `metadata.json`, `quality_report.json`, `chunks.jsonl`, plus CSV. Chaque
  article exporté porte les métadonnées de son document et sa page source —
  un moteur RAG peut citer sa source sans jointure. Parquet reste hors MVP et
  est explicitement signalé comme tel.
- Commandes `python -m bldp stats` et `python -m bldp trace`.

## Phase 9 — livré

### Module 9 — doublons (§14)

- `bldp/core/dedup.py` : quatre types de liens, du plus certain au plus
  incertain — `file_hash`, `text_hash`, `similarity` (Jaccard sur shingles),
  `partial` (taux d'inclusion, qui rattrape le cas qu'un Jaccard raterait : un
  extrait de 10 pages noyé dans un recueil de 400).
- **Aucun document n'est supprimé** : `dedup.action` autre que `flag` est
  explicitement refusé et journalisé. Les liens sont marqués, la décision
  revient à un humain.
- Comparaison aussi avec le corpus déjà en base (`load_known_hashes`) et
  détection des pages dupliquées à l'intérieur d'un même document.

### Module 10 — contrôle qualité (§15)

- `bldp/core/validation/quality.py` : score composite pondéré — texte 50 %,
  structure 35 %, métadonnées 15 %.
- Anomalies détectées : pages sans texte, pages manquantes, pages dupliquées,
  caractères anormaux, OCR fragmenté, articles incomplets ou anormalement
  longs, numérotation incohérente, numéros illisibles, métadonnées absentes ou
  peu fiables, doublons.
- Deux plafonds durs : toute anomalie de gravité `error` plafonne le score, et
  un document nécessitant un OCR mais extrait en natif ne peut pas dépasser
  0,40 — un corpus amputé ne doit jamais passer pour propre.

### Validation humaine (§16)

- `suggest_validation()` ne renvoie **jamais** `valide` : au mieux
  `en_attente`. Le système ne s'auto-valide pas ; il trie ce qu'un humain doit
  regarder en priorité.
- `comparison_view()` fournit les trois couches côte à côte : page originale
  (texte brut), page nettoyée, article structuré.
- `review_queue()` ordonne les documents du plus problématique au moins.

## Phase 10 — livré

- `bldp/core/relations.py` (module 8) : détection des relations déclarées
  (`abroge`, `abroge_partiellement`, `modifie`, `remplace`, `applique`, `cite`),
  résolution des cibles dans le corpus, propagation du statut.
- Normalisation stricte des références : « loi n° 2015 - 18 » et
  « loi n° 2015-018 » sont reconnues comme le même texte, mais une formule
  vague (« toutes dispositions antérieures contraires ») n'est **jamais**
  rapprochée d'un texte précis — elle ne change donc aucun statut.
- Trois garde-fous conformes au « semi-automatique » du §13 :
  1. une cible non résolue est conservée avec sa citation brute et
     `needs_review=True` — l'information attend le texte manquant ;
  2. sous le seuil `relations.min_confidence`, le statut n'est pas modifié,
     seulement signalé ;
  3. toute modification de statut est journalisée avec sa preuve et porte la
     mention « à confirmer par un juriste ».
- Hiérarchie de gravité des statuts : abrogé > remplacé > partiellement abrogé
  > modifié. Un statut ne régresse jamais.
- Auto-références et cibles hors lot détectées et signalées plutôt qu'appliquées.
- Versions : deux documents de même type et même numéro officiel sont regroupés
  et numérotés chronologiquement, avec avertissement.

## Phases 11 et 12 — livré

### Chunking (§20)

- `bldp/core/chunking.py` : découpage **juridiquement conscient**, suivant la
  priorité `Article → alinéas → paragraphes`.
  1. un article qui tient reste **entier** — c'est l'unité de citation du droit ;
  2. un article trop long est découpé sur ses alinéas, jamais à l'intérieur ;
  3. un alinéa unique démesuré est coupé sur des frontières de **phrase**.
- Tolérance de 20 % : un article dépassant la cible de quelques mots n'est pas
  fragmenté pour autant.
- Chaque chunk porte document, article, chapitre, section, page source,
  position, plus le numéro et la date du texte : un résultat de recherche peut
  citer sa source sans jointure.
- Repli par page quand aucun article n'est détecté, avec mention explicite de
  la stratégie dégradée — plutôt que de ne rien produire.
- Trois stratégies configurables : `article` (défaut), `alinea`, `fixed`.

### Embeddings (§19)

- `bldp/core/embeddings.py` : Sentence Transformers, **désactivé par défaut**
  (§4). Le modèle est chargé paresseusement — importer le module ne télécharge
  jamais rien. Exécution locale sur CPU, aucun envoi externe (§27).
- Chaque `EmbeddingRecord` conserve `document_id`, `article_id`,
  `article_number`, le texte et le nom du modèle (§19).
- Recherche exhaustive de secours sans dépendance (`brute_force_search`).

### FAISS (§4)

- `bldp/core/vectorstore.py` : index `IndexFlatIP` (cosinus sur vecteurs
  normalisés), persisté avec un fichier de métadonnées parallèle.
- **Un index sans ses métadonnées est refusé au chargement** : il ne
  permettrait pas de citer ses sources, donc il est inutilisable pour du droit.
- Désynchronisation index/métadonnées détectée et rejetée.
- Qdrant explicitement déclaré hors périmètre du MVP.
- L'indisponibilité de FAISS n'échoue jamais un corpus par ailleurs complet :
  l'indexation est simplement ignorée avec avertissement.

## Phase 13 — livré

- `bldp/pipeline.py` : orchestration complète suivant l'architecture du §5,
  `Loader → Classifier → PyMuPDF/OCR → Normalizer → Parser → Metadata →
  Relations → Dedup → Quality → SQLite/JSONL → (Chunking → Embeddings → FAISS)`.
- **§26 appliqué à la lettre** : chaque document est traité dans son propre bloc
  protégé ; un PDF corrompu, une exception inattendue ou un OCR indisponible
  n'arrêtent pas le lot. Le bilan distingue réussis / à vérifier / en échec, et
  les trois catégories sont mutuellement exclusives.
- Le document en échec **reste dans le corpus** avec son erreur, plutôt que de
  disparaître silencieusement.
- Deux artefacts de traçabilité écrits à chaque exécution :
  `run_report_<horodatage>.json` et `pipeline_summary.json`.
- Idempotence : deux exécutions successives ne dupliquent rien en base, et la
  validation humaine survit au retraitement.
- CLI complète, toutes les commandes du §21 : `ingest`, `process`, `validate`,
  `embed`, `export`, `pipeline`, plus `analyze`, `extract`, `parse`, `search`,
  `trace`, `stats`, `doctor`, `config`.
- `bldp validate --document <id>` affiche les trois couches du §16 côte à côte :
  page d'origine (brut) ↔ page nettoyée ↔ article structuré, et
  `--set-status valide|a_verifier|rejete` enregistre la décision.

### Défaut corrigé pendant cette phase

Un test passait isolément mais échouait en suite complète : `setup_logging`
fixe `propagate = False` sur le logger `bldp`, ce qui rendait `caplog` aveugle
pour **tous les tests suivant** un test invoquant la CLI. Fuite d'état global
entre tests, désormais neutralisée par une fixture de restauration.

## Phase 14 — livré

- `bldp/web/app.py` + `templates/index.html` : interface FastAPI **volontairement
  minimale**, couvrant exactement les huit fonctions du §22 — dépôt d'un PDF,
  lancement, progression, aperçu du texte, aperçu des articles, affichage des
  erreurs, validation manuelle, téléchargement des exports.
- **Aucune ressource externe** : pas de CDN, pas de police distante, CSS et JS
  inline (§27). Un test vérifie mécaniquement l'absence d'URL distante.
- Écoute limitée à `127.0.0.1` par défaut, avec avertissement si l'on ouvre au
  réseau : le corpus peut contenir des textes non encore publiés.
- Traitement en arrière-plan avec suivi de progression ; une tâche en échec ne
  fait jamais tomber le serveur.
- La route de téléchargement refuse tout chemin composé (traversée de
  répertoire testée).
- Commande `python -m bldp serve`.

### Trois défauts trouvés pendant cette phase

1. **Une citation requalifiait le document.** Un décret « portant application
   de la loi n° 2026-001 » était typé `loi`, parce que le motif d'une loi
   correspondait à la *citation* et que l'ordre du dictionnaire décidait. C'est
   désormais la correspondance **la plus précoce** qui fait foi : l'intitulé
   d'un texte précède ses citations. Erreur de qualification juridique évitée.
2. **`--set _root=…` était silencieusement écrasé** par `load_config`. Les
   tests de la CLI écrivaient donc dans le `data/` du dépôt au lieu de leur
   dossier temporaire. La racine explicite est maintenant honorée, et une
   option `--root` propre a été ajoutée.
3. **L'identifiant de tâche fuyait dans le corpus** : un fichier déposé
   devenait `0f76934c5acf_loi` au lieu de `loi`. Chaque dépôt a désormais son
   propre sous-dossier, ce qui préserve le nom d'origine.

## Phase 15 — livré

- `README.md` complet : installation, utilisation, architecture, formats,
  exemples, **limites connues**, contribution, licence (§28).
- `docs/ARCHITECTURE.md` : explique le *pourquoi* des décisions de conception,
  avec un tableau « ce que BLDP fait / ne fait pas » face à chaque situation
  ambiguë.
- `LICENSE` (Apache-2.0), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `CHANGELOG.md`.
- CI GitHub Actions : matrice Python 3.11/3.12/3.13 × Ubuntu/Windows, plus un
  job dédié exécutant la suite **avec** Tesseract et Ghostscript installés.
- Modèles d'issue et de pull request rappelant les exigences du projet
  (impact sur les corpus existants, tests de non-régression sur le contenu
  juridique, aucun document réel versionné).
- `.gitignore` corrigé : les données restent hors du dépôt, mais l'arborescence
  de travail (`input/`, `data/`) est préservée par ses `.gitkeep` — Git ne
  redescend pas dans un dossier exclu sans une règle `!<dossier>/**/`.
- Couverture mesurée : 82 % (les zones basses sont les chemins optionnels
  FAISS et OCR, non installés sur la machine de développement).

### Trois défauts trouvés par la vérification finale de bout en bout

1. **`FOREIGN KEY constraint failed` : les vecteurs étaient perdus.** Le
   pipeline persistait chunks et embeddings *avant* d'exporter les documents en
   base ; la contrainte échouait, l'exception était avalée par un `except`
   large, et les embeddings disparaissaient sans bruit. La persistance a lieu
   désormais après l'export.
2. **Le dernier article absorbait le bloc de signature.** « Fait à Cotonou, le
   10 février 2026 » se retrouvait dans le texte normatif du dernier article.
   La formule de promulgation borne maintenant les articles, et le texte qui la
   suit est conservé à part dans `ParseResult.epilogue` — rien n'est jeté.
3. **Le titre du document était faux.** Le nettoyage recolle les retours à la
   ligne artificiels, fusionnant « LOI N° 2026-001 … » et « portant … » ; la
   détection exigeait « portant » en début de ligne et retombait alors sur
   « ASSEMBLEE NATIONALE ». Corrigé, avec au passage la récupération des
   articles contenus dans les annexes, qui étaient auparavant ignorés.

---

# Confrontation à un corpus réel (2026-08-30)

20 lois béninoises authentiques (377 pages, 2025-2026), dont **19 scans purs**
sans aucune couche texte. OCR installé : Tesseract 5.4 + pack `fra`,
Ghostscript 10.07.1, OCRmyPDF 17.11.

Ce test a révélé **six défauts** que les documents fabriqués ne pouvaient pas
exposer. Trois d'entre eux corrompaient silencieusement le corpus.

### 1. Mojibake sur tout le texte OCR — *critique*

`subprocess.run(..., text=True)` décodait la sortie de Tesseract avec
l'encodage local (cp1252 sur Windows) alors qu'elle est en UTF-8. « présente »
devenait « prÃ©sente ». **Chaque mot accentué du corpus était corrompu.**
Corrigé par `encoding="utf-8"` sur les quatre appels, verrouillé par un test
qui vérifie que tout appel déclare son encodage.

### 2. La formule d'ouverture prise pour une clôture — *critique*

Un texte béninois commence par :

> Le Président de la République promulgue la loi dont la teneur suit :

Le motif générique de signature (`le président de la république` en début de
ligne) y voyait une **fin** de partie normative. Conséquence : **13 documents
sur 20 ne produisaient aucun article** — le corpus ne conservait qu'un
préambule. Après correction : 0 document sans article, 742 articles extraits.

Introduction de `RuleSet.never_stop_patterns`, prioritaire sur les règles
d'arrêt, listant les formules qui *ouvrent* un texte (`promulgue`,
`teneur suit`, `a délibéré et adopté`).

### 3. Le numéro officiel du document était celui d'un autre texte — *critique*

L'OCR rend le séparateur de « LOI n° 2025 — 18 » par un **cadratin** (U+2014),
que les motifs (`[-–]`) ne couvraient pas. Le premier numéro reconnu était
alors celui de la loi **citée** dans l'intitulé : la loi 2025-18 était
enregistrée sous le numéro `2022-09`. Pour un corpus juridique, c'est une
erreur d'identification.

Deux corrections complémentaires : une classe `DASH_CLASS` couvrant tous les
tirets Unicode, et une normalisation des tirets **entre chiffres** au nettoyage
(les tirets d'incise restent intacts, car ce sont de la ponctuation).

### 4. La formule de promulgation est un article numéroté

Au Bénin, `Article 2 : La présente loi sera exécutée comme Loi de l'État` est
un article voté. Le motif d'arrêt le supprimait. Une ligne d'arrêt doit
désormais **ne pas être elle-même un en-tête reconnu**.

### 5. La date retenue pouvait être celle d'un texte cité

« LOI n° 2025-18 du 25 juillet 2025 modifiant la loi n° 2022-09 **du 27 juin
2022** » : la date d'un autre texte pouvait l'emporter. La date est maintenant
cherchée d'abord dans la ligne portant le numéro officiel ; hors intitulé, la
confiance tombe à 0,55 et la preuve porte la mention « à vérifier ».

### 6. `check_ocr_ready` mentait

Avec le paquet pip `ocrmypdf` installé mais sans le binaire Tesseract, le
diagnostic annonçait « OCR opérationnel : OUI » — puis chaque document
échouait. OCRmyPDF n'est qu'un **pilote** de Tesseract. Le diagnostic vérifie
désormais Tesseract et la lisibilité de `TESSDATA_PREFIX`.

### 7. Les articles longs disparaissaient — *critique*

Après recollage des lignes, l'article 38 de la loi 2025-09 faisait 413
caractères, au-delà du `max_line_length` de la règle : celle-ci refusait de
l'examiner et **l'article disparaissait sans avertissement**. 46 articles
étaient ainsi perdus sur le corpus.

Le garde-fou visait à empêcher qu'un paragraphe soit pris pour un titre ; or
pour un article, ce qui suit le numéro *est* le contenu normatif. Avec
`content_is_body`, la limite ne porte plus que sur le préfixe
(« Article 38 : »).

### 8. Les en-têtes éclatés par l'OCR étaient ignorés — *critique*

Tesseract rend fréquemment ::

    Article
    88
    :

Le parser, qui raisonne ligne par ligne, ne reconnaissait pas l'article — 13
de plus perdus. Le recollage général refuse de joindre une ligne commençant par
un chiffre (pour ne pas avaler une pagination), d'où une règle dédiée,
volontairement étroite : au plus trois lignes, et seulement si la suite est un
numéro ou un deux-points.

### Résultat

| | avant | après |
|---|---|---|
| documents sans aucun article | 13 / 20 | **0 / 20** |
| articles extraits | ~90 | **801** |
| alinéas | — | **1 552** |
| anomalies de numérotation | 98 | **62** |
| documents en échec | 0 | **0** |
| **en-têtes présents dans le texte mais non extraits** | 74 | **0** |

La dernière ligne est le contrôle décisif : un audit indépendant compare les
`Article N` présents dans le texte OCRisé aux articles réellement extraits.
**Aucun article n'est perdu.**

Les 15 documents restants sont marqués « à vérifier », principalement pour
numérotation incohérente — ce qui est **attendu** : ces lois modificatives
citent des articles d'autres textes (`Article 5 nouveau`, `Article 12-1`), et
le contrôle qualité signale à juste titre que la séquence n'est pas continue.
C'est un signalement, pas une erreur d'extraction.


---

# Passage à l'échelle (2026-08-31)

Trois mécanismes ajoutés après la confrontation au corpus réel, mesurés sur les
mêmes 20 lois.

| | avant | après |
|---|---|---|
| relancer un lot déjà traité | 11 min | **4,5 s** (`--resume`) |
| 6 lois, 1 fil | 105 s | — |
| 6 lois, 4 fils | — | **56 s** (×1,9) |
| PDF OCRisés conservés | tous | **seulement les documents à vérifier** |

### Ce qui a demandé le plus d'attention

Le piège n'était pas la reprise elle-même, mais ses effets de bord :

1. **Les exports auraient été tronqués.** `export_all` écrit `documents.jsonl`
   à partir du lot en mémoire. Avec la reprise, ce lot ne contient que les
   nouveaux documents : le fichier aurait silencieusement perdu tout le reste
   du corpus. D'où `_export_corpus`, qui régénère depuis la base.
2. **La reconstruction depuis la base était partielle.** Elle ne restaurait ni
   la structure, ni les relations, ni les doublons — les exports régénérés
   auraient donc perdu la hiérarchie juridique. `load_document()` restaure
   désormais tout ce qui est persisté.
3. **Un document en échec ne doit pas être considéré comme fait.** La reprise
   ne saute que les documents sans erreur, sinon un verrou de fichier ou un OCR
   interrompu deviendrait une perte définitive.
4. **Deux niveaux de parallélisme se multiplient.** OCRmyPDF parallélise déjà
   en interne ; avec N fils Python, on saturait la machine. Il est bridé à un
   fil quand `workers > 1`.

### Défaut trouvé en chemin

Une suppression de code trop large a fait disparaître `cmd_serve` : le parseur
d'arguments se construisait sans erreur, mais la commande explosait à
l'exécution. Un test vérifie désormais que **chaque commande déclarée possède
son gestionnaire** — la classe d'erreur est fermée.


---

# Second corpus réel : scans dégradés, cinq nouveaux types (2026-08-31)

24 documents — accord, arrêté, décision, décret, ordonnance — dont l'OCR est
nettement plus abîmé que celui des 20 lois : `DÉGRET` pour `DÉCRET`, `N"` pour
`N°`, `Articte` pour `Article`, `Article 1"'` pour `Article 1er`.

| | avant | après |
|---|---|---|
| documents sans numéro officiel | 13 / 24 | **0 / 24** |
| documents typés à tort « constitution » | 13 | **0** |
| articles extraits | 104 | **216** |
| alinéas | — | **336** |
| en-têtes présents mais non extraits | 74 | **0** |
| documents en échec | 0 | **0** |

### Le défaut de fond : les visas

Tout texte réglementaire s'ouvre sur ses visas, qui citent d'autres textes avec
leurs types, numéros et dates. Tant que l'intitulé propre était lisible, le
principe « la correspondance la plus précoce fait foi » suffisait. Dès que
l'OCR l'abîme, ce principe fait basculer *toutes* les métadonnées vers les
textes cités : un arrêté de 2018 devenait un décret de 1990.

La correction est une règle unique, appliquée uniformément au type, au numéro,
à la date et au titre : **chercher d'abord dans le document débarrassé de ses
citations**. Le repli sur le texte complet reste possible — sinon on perdrait
de l'information — mais à confiance 0,40 et avec la mention « visa — à
vérifier ».

C'est le principe du §33 appliqué à un cas nouveau : ne pas deviner, signaler.

### Ce qui reste, et qui est correct

Quatre documents portent encore un numéro venu d'un visa, à confiance 0,40 :
leur intitulé est trop dégradé pour être lu. Le pipeline le dit au lieu de
prétendre — c'est le comportement voulu. Ils sont dans la file de validation
humaine.

### Note sur le classement

Les dossiers `accord/` et `decision/` de ce lot contiennent en réalité des
**décrets** (classement par sujet, non par nature). Le pipeline se fie au texte
et les type correctement en `decret` : le classement manuel n'est qu'un indice
de faible confiance, conformément au §6.


---

# Analyse dirigée et corrections (2026-08-31)

Analyse systématique des rapports qualité des deux corpus, puis correction des
sept défauts identifiés — plus un huitième trouvé en chemin.

## Effet mesuré

### Corpus `data-test` (24 documents, OCR dégradé)

| | avant | après |
|---|---|---|
| anomalies de numérotation | 57 | **33** |
| numéros d'article dupliqués | 37 | **13** |
| articles signalés incomplets | 14 | **6** |
| numéros illisibles honnêtement signalés | 0 | **10** |

### Corpus des 20 lois (non-régression)

| | avant | après |
|---|---|---|
| articles | 801 | **810** |
| alinéas | 1 552 | **1 616** |
| documents ayant perdu des articles | — | **0** |
| en-têtes présents mais non extraits | 0 | **0** |

Les 9 articles supplémentaires sont réels : un par document sur neuf lois,
principalement l'article de promulgation, désormais correctement ordonné.

## Le défaut le plus instructif

Le tri des blocs par position corrige l'ordre de lecture — mais **pas** avec
l'option `sort=True` de PyMuPDF, que j'ai mesurée avant d'adopter : elle recolle
les blocs sans séparateur et soude le dernier mot de l'un au premier du suivant
(« gouvernementvu », « etvu », « ministèresvu »).

Sur un corpus juridique où « Vu » introduit chaque visa, cette soudure aurait
détruit la détection des citations que je venais de construire. Le tri est donc
fait manuellement sur les blocs, séparateur conservé : **0 mot perdu, 0 mot
soudé**, pour le même gain d'ordre.

C'est le genre de piège qu'on ne voit qu'en mesurant : la fonctionnalité
« marchait » et améliorait le classement des articles.

## Principe réaffirmé

Deux corrections consistent à **refuser d'interpréter** :

* un romain non canonique (« ICI », « vlII ») ne reçoit plus de valeur
  inventée ;
* un numéro capturé tronqué au milieu d'un mot est rejeté plutôt que produit
  à 0,92 de confiance.

Dans les deux cas, le document part en validation humaine. C'est moins
spectaculaire qu'une valeur remplie, et c'est plus juste.
