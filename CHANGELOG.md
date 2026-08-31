# Journal des modifications

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le
projet adhère au [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté — registre de suivi des documents

- **Tickets, étapes et journal** (`bldp suivi`). Chaque document reçoit un
  ticket attaché à **l'empreinte de son contenu**, pas à son nom de fichier :
  le même texte reçu deux fois retrouve son ticket, son historique et la
  décision humaine déjà prise. C'est la garantie de ne pas refaire un travail
  déjà fait.
- **Journal en écriture seule** : date, auteur et motif de chaque changement.
  La traçabilité du §33, jusque-là assurée sur le *contenu*, s'étend aux
  *décisions*.
- **Transitions contrôlées** : on ne valide pas un document jamais traité, et
  `valide`/`rejete` exigent un acteur humain nommé (§16). Une décision reste
  révocable — un juriste peut rouvrir un dossier sans toucher la base.
- **Reprise étendue** : `--resume` écarte aussi les documents dont un humain a
  réglé le sort, pour ne pas remplacer sa décision par un verdict automatique.

### Corrigé — codes juridiques (hiérarchie profonde)

Deux codes béninois — code du travail (317 articles) et code électoral (211) —
ont exposé trois défauts.

- **Les corrections OCR ne s'appliquaient qu'à notre propre OCR.** Un PDF peut
  arriver avec une couche texte produite par l'OCR de quelqu'un d'autre : le
  pipeline le classe « natif » alors que son texte porte toutes les confusions
  d'un scan. Dans un code réputé natif, « Arlicle » apparaissait **75 fois**,
  et autant d'articles disparaissaient. Ce qui compte n'est pas qui a produit
  l'OCR, mais si le texte en porte les traces.
- **Variantes de « Article » élargies** : `Arlicle`, `Artlcle`, `Arllcle`,
  `ArtIcle`, `Articte`, `ArUcle`, `Adicle`. Le motif reste étroit — sept
  lettres bornées, casse respectée — et n'atteint aucun mot français.
- **Mots-clés de subdivision** : `TIVRE` pour `LIVRE` (le niveau LIVRE d'un
  code disparaissait entièrement), et le mot-clé soudé à son numéro
  (`TITREll`, `CHAPITREI`, `SECfIONl`).
- **Désignations sans chiffre** : `LIVRE PRÉLIMINAIRE`, `TITRE UNIQUE` sont
  des subdivisions réelles que les règles exigeant un numéro ignoraient.
  Introduites dans `SUBDIVISION_NUMBER`, distinct de `NUMBER` afin que
  « Article unique » reste une forme déclarée par la juridiction (§29).
- **Le compteur de corrections comptait les remplacements sans effet** : un
  document sain semblait avoir été réparé. Les motifs larges portent
  désormais un `(?!Article)`.

Résultat : code électoral 92 → **186 articles sur 211**, code du travail
292 → **296 sur 317**, subdivisions 60 → **92**, niveau LIVRE de 0 à 6.

### Corrigé — analyse dirigée du corpus dégradé

Sept défauts identifiés par une analyse systématique des rapports qualité,
puis corrigés.

- **Romains OCRisés invalides.** `roman_to_int` acceptait toute suite de
  lettres romaines : « ICI » valait 100, « vlII » valait 47. Ces rangs inventés
  créaient de fausses ruptures de numérotation. Seule la **forme canonique**
  est désormais acceptée ; le reste est signalé
  `numero_article_non_interpretable`.
- **Numéro capturé tronqué au milieu d'un mot.** « N"2olo.- Oü8 » (le « 2 » de
  028 détruit par l'OCR) aurait donné « 2010-0 » à 0,92 de confiance — un
  numéro faux annoncé comme sûr. Un garde-fou refuse la capture et renvoie le
  document vers la validation humaine.
- **Séparateurs `.` et `.-` acceptés** dans un numéro officiel, sans toucher
  au texte : les dates « 11.12.1990 » et les décimales restent intactes.
- **`_NEXT_REFERENCE_RE` utilisait encore le motif strict** : sur OCR dégradé,
  la troncature de l'intitulé échouait en silence et la date d'un texte cité
  pouvait être retenue à pleine confiance.
- **`_mentions_number` ne neutralisait que deux tirets** : la comparaison
  échouait sur les numéros rendus avec un cadratin ou un underscore.
- **Textes annexés fusionnés dans la numérotation du corps.** Un accord annexé
  recommence à « Article premier » : ses articles se mêlaient à ceux du
  document, produisant des identifiants en collision et des dizaines de fausses
  ruptures. Un **nœud d'annexe implicite** leur rend leur contexte, et leurs
  identifiants portent désormais leur portée.
- **`check_numbering` comparait des séquences hétérogènes.** La vérification se
  fait maintenant **par annexe**, et un retour à « 1 » y ouvre une nouvelle
  série — plusieurs accords peuvent être annexés à un même texte. Dans le corps
  d'un document, en revanche, tout retour en arrière reste signalé.

### Ajouté

- **Remise des blocs dans l'ordre de lecture** (`extraction.sort_blocks`, actif
  par défaut). L'ordre stocké dans un PDF numérisé ne suit pas toujours la
  lecture : les articles ressortaient « 21, 23, 24, 22 ».

  Le tri est fait sur les blocs, séparateur conservé, et **non** via l'option
  `sort=True` de PyMuPDF : celle-ci soude le dernier mot d'un bloc au premier
  du suivant (« gouvernementvu »). Sur un corpus où « Vu » introduit les visas,
  cette soudure est une corruption de contenu. Mesuré : 0 mot perdu, 0 mot
  soudé, retours en arrière divisés par 2,5.

### Corrigé — scans de mauvaise qualité, cinq nouveaux types de documents

24 documents béninois (accord, arrêté, décision, décret, ordonnance) au texte
nettement plus abîmé que le lot précédent ont exposé une famille entière de
défauts : **quand l'OCR abîme l'intitulé, toutes les métadonnées dérivent vers
les visas**, c'est-à-dire vers les textes *cités*.

- **Le symbole « ° » mal lu bloquait tout.** L'OCR rend « N° 2019 » par
  « N" 2019 » ou « N' 2019 » ; aucun numéro officiel n'était alors reconnu, et
  sans numéro le type et la date se rabattaient sur les visas. 13 documents sur
  24 étaient sans numéro. Ajout de `NUMERO_PREFIX` et `OCR_DIGIT`, plus
  `normalize_ocr_number` pour réparer « 2018-OO1 » → « 2018-001 ».
- **Les visas définissaient le document.** Toutes les métadonnées sont
  désormais cherchées d'abord dans le texte débarrassé des citations
  (`strip_citation_lines`) ; le repli sur le texte complet reste possible mais
  à confiance 0,40-0,45 et avec une preuve qui le signale.
- **« portant Constitution de la République du Bénin » requalifiait 13
  documents.** Cette formule désigne toujours la loi 90-32, visée par presque
  tout : le motif est maintenant ancré en début de ligne.
- **Une simple mention de la Cour constitutionnelle rendait un décret
  « jurisprudence ».** La juridiction doit être l'émetteur, en début de ligne.
- **Les textes annexés étaient perdus.** Une ordonnance de ratification
  perdait les 10 articles de l'accord qu'elle approuve, parce qu'ils suivent la
  promulgation sans être introduits par le mot « ANNEXE ». Une **série** d'au
  moins deux articles rouvre désormais la détection ; un article isolé après
  signature reste écarté.
- **« Article 1"' » (OCR de « Article 1er ») n'était pas reconnu** : guillemets
  et apostrophes acceptés comme séparateurs.
- **« Articte », « Artide » etc.** ajoutés aux corrections OCR, sur liste
  explicite pour ne pas transformer « Artisan » en en-tête.
- **Classement en dossiers** : singulier comme pluriel acceptés
  (`decret/` comme `decrets/`), plus ordonnance, décision, accord, convention.

### Corrigé — confrontation à un corpus béninois réel

20 lois authentiques (377 pages, 19 scans purs) ont révélé six défauts que des
documents fabriqués ne pouvaient pas exposer.

- **Mojibake sur tout le texte OCR.** La sortie de Tesseract, en UTF-8, était
  décodée avec l'encodage local (cp1252 sur Windows) : chaque mot accentué du
  corpus était corrompu.
- **Formule d'ouverture prise pour une clôture.** « Le Président de la
  République promulgue la loi dont la teneur suit » était traité comme une fin
  de partie normative : 13 documents sur 20 ne produisaient aucun article.
  Ajout de `RuleSet.never_stop_patterns`.
- **Numéro officiel emprunté à un texte cité.** L'OCR rend le séparateur de
  « n° 2025 — 18 » par un cadratin (U+2014), non couvert par les motifs ; le
  premier numéro reconnu était celui de la loi citée. Ajout de `DASH_CLASS` et
  normalisation des tirets entre chiffres au nettoyage.
- **Article de promulgation supprimé.** Au Bénin, « Article 2 : La présente loi
  sera exécutée comme Loi de l'État » est un article voté. Une ligne d'arrêt ne
  peut plus être un en-tête reconnu.
- **Date empruntée à un texte cité.** La date est cherchée dans l'intitulé
  propre du document, tronqué avant la référence suivante ; hors intitulé, la
  confiance tombe à 0,55 avec mention « à vérifier ».
- **`check_ocr_ready` annonçait un OCR inopérant.** OCRmyPDF sans Tesseract
  était déclaré prêt. Le diagnostic vérifie désormais Tesseract et la
  lisibilité de `TESSDATA_PREFIX`.
- **Articles longs perdus.** Un article dépassant `max_line_length` une fois
  les lignes recollées n'était pas examiné et disparaissait sans avertissement
  (46 articles). La limite ne porte plus que sur le préfixe de l'en-tête
  (`content_is_body`).
- **En-têtes éclatés par l'OCR ignorés.** « Article \n 88 \n : » n'était pas
  reconnu (13 articles). Ajout de `rejoin_split_article_headers`, règle étroite
  appliquée avant la suppression des numéros de page.

### Ajouté — passage à l'échelle

- **Reprise incrémentale** (`--resume`, `pipeline.resume`) : les documents déjà
  traités avec succès sont sautés. Sur le corpus de 20 lois, une reprise passe
  de 11 min à 4,5 s. Un document ayant échoué est toujours retenté — une panne
  transitoire ne doit pas devenir une perte définitive.
- **Traitement parallèle** (`--workers`, `pipeline.workers`) : ×1,9 mesuré sur
  4 fils, pour un résultat strictement identique au séquentiel. L'ordre suit
  l'entrée et non l'achèvement, afin que deux exécutions produisent le même
  corpus. OCRmyPDF est automatiquement bridé à un fil interne quand plusieurs
  documents sont traités de front.
- **Rétention sélective des PDF OCRisés** (`--keep-ocr`,
  `ocr.keep_sidecar_for`) : `all`, `review` (ne garder que les documents à
  vérifier) ou `none`. La suppression est journalisée dans le document, jamais
  silencieuse.
- `sqlite_store.load_document()` / `load_documents()` : reconstruction
  **complète** d'un document depuis la base — structure, relations et doublons
  compris. L'ancienne reconstruction, partielle, faisait perdre la hiérarchie
  et les relations juridiques aux exports régénérés.

### Corrigé

- Avec `--resume`, les exports sont désormais régénérés depuis le corpus
  complet. N'exporter que le lot courant aurait tronqué `documents.jsonl` et
  `articles.jsonl` au dernier lot traité, effaçant silencieusement le reste.
- Le bilan d'exécution renseigne les documents sautés même lorsque *tout* a été
  sauté (sortie anticipée).

### Ajouté

- Contrôle qualité `date_incoherente_avec_le_numero` : signale un écart entre
  le millésime du numéro officiel et l'année de la date, avec une tolérance
  contextuelle pour les textes adoptés au changement d'année.
- README : procédure d'installation OCR détaillée pour Windows, sans droits
  administrateur.

## [0.1.0] — 2026-08-30

Première version : MVP complet, couvrant les 15 critères de réussite du cahier
des charges.

### Ajouté

**Importation et extraction**

- Inventaire d'un dossier avec empreinte SHA-256 et identifiants stables ; les
  originaux ne sont jamais modifiés.
- Analyse de chaque PDF et décision d'OCR **motivée**, avec un score de
  confiance qui s'effondre au voisinage des seuils.
- Extraction native PyMuPDF page par page, provenance conservée.
- OCR via OCRmyPDF et Tesseract, avec repli mutuel et OCR ciblé par page.

**Nettoyage**

- Suppression des artefacts techniques : caractères de contrôle, césures,
  retours à la ligne artificiels, en-têtes et pieds de page répétés, numéros de
  page isolés, lignes décoratives.
- Veto de protection sur 12 familles de motifs juridiques : une ligne portant un
  article, un alinéa, une référence, une date, un montant, une sanction ou une
  condition ne peut être supprimée par aucune règle.
- `CleaningReport` journalisant chaque suppression ; alerte au-delà de 25 % de
  texte retiré.

**Structure et articles**

- Parser modulaire piloté par règles, couvrant les 10 niveaux hiérarchiques.
- Formes d'articles : `Article 1`, `Article 1er`, `Article premier`, `Art. 1`,
  `45 bis`, `45-2`, `Article unique`, `Article 45 nouveau`.
- Découpage en alinéas dans l'ordre ; contexte hiérarchique complet par article.
- Détection des ruptures de numérotation, signalées et jamais corrigées.

**Métadonnées et relations**

- Métadonnées avec score de confiance et preuve pour chaque champ deviné.
- Fichier de métadonnées manuel (`<doc>.meta.yaml|json`) prioritaire.
- Relations `modifie` / `abroge` / `remplace` avec résolution des cibles et
  propagation prudente des statuts.
- Regroupement et numérotation des versions d'un même texte.

**Qualité et validation**

- Score composite (texte 50 %, structure 35 %, métadonnées 15 %).
- Détection des doublons en quatre catégories, jamais de suppression.
- Vue de comparaison à trois couches pour la validation humaine.
- Le système ne se valide jamais lui-même.

**Sorties**

- Base SQLite en 11 tables, exports JSONL, JSON, CSV.
- `trace_article()` remontant d'un article à sa page et à son fichier source.
- Chunking juridiquement conscient ; embeddings et index FAISS optionnels.

**Interface**

- CLI complète (15 commandes).
- Interface web minimale FastAPI, sans aucune ressource externe.

### Sécurité et confidentialité

- Traitement entièrement local ; `privacy.allow_external_calls` vaut `false`.
- Le serveur web n'écoute que sur `127.0.0.1` par défaut.
- La route de téléchargement refuse tout chemin composé.

### Notes

- Le format Parquet reste hors périmètre, comme prévu au cahier des charges.
- Le backend vectoriel Qdrant est prévu pour une version ultérieure.
