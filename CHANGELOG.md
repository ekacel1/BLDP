# Journal des modifications

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le
projet adhère au [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

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
