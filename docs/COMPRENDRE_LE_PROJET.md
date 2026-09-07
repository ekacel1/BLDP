# Comprendre le projet BLDP — Parcours de lecture pour agent local

> **Destinataire :** Ce document est conçu pour un agent d'intelligence artificielle ou un intervenant découvrant le projet. Il vous explique la finalité de **BLDP (Benin Legal Data Pipeline)** et vous guide pas à pas à travers la documentation existante pour en acquérir une compréhension exhaustive.

---

## 1. Ce qu'est BLDP en 3 phrases

1. **Le but** : Transformer l'ensemble des documents juridiques officiels de la République du Bénin (décrets, lois, ordonnances publiés sous forme de PDF scannés ou natifs) en un **corpus numérique structuré, propre, auditable et vérifiable**, prêt pour l'analyse et la recherche/RAG.
2. **Le moyen** : Une chaîne de traitement automatisée modulaire (inventaire, décision d'OCR, extraction page par page, nettoyage d'en-têtes/pieds-de-page, découpage en articles, réconciliation avec les catalogues officiels, calcul de score de qualité).
3. **Le principe premier** : **Exactitude > automatisation.** En cas de doute juridique, le système ne devine jamais. Il signale, alerte et requiert une validation humaine (§33 du Cahier des charges).

---

## 2. L'architecture globale à trois machines

Pour comprendre le rôle de chaque élément, gardez ce schéma en mémoire :

```text
  Portail officiel SGG (https://sgg.gouv.bj)
                   │
                   ▼ (1 requête polie / seconde)
  ┌──────────────────────────────────────────────────┐
  │ 1. LE VPS (191.96.1.191)                         │
  │    • Allumé en permanence, pilote les cycles.    │
  │    • Stocke temporairement les sources (17 Go).  │
  └────────┬─────────────────────────────────────────┘
           │ upload temporaire
           ▼
  ┌──────────────────────────────────────────────────┐
  │ 2. LA VM GOOGLE COLAB (Éphémère, gratuite)       │
  │    • 2 cœurs CPU, Tesseract 5, Ghostscript.      │
  │    • Fait le calcul lourd (OCR, parsing).        │
  │    • Produit l'archive ZIP certifiée et la rend. │
  └────────┬─────────────────────────────────────────┘
           │ envoi de l'archive ZIP
           ▼
  ┌──────────────────────────────────────────────────┐
  │ 3. LE TÉLÉPHONE / TERMUX (Destination finale)     │
  │    • Tire les fichiers depuis le VPS (rsync).    │
  │    • Vérifie les empreintes SHA-256 et comptes.  │
  │    • Conserve la COPIE DURABLE DÉFINITIVE.       │
  │    • Émet un reçu autorisant la libération VPS.  │
  └──────────────────────────────────────────────────┘
```

---

## 3. Parcours de lecture guidé de la documentation

Pour comprendre le projet dans les moindres détails, lisez les documents existants dans cet ordre précis :

### Étape 1 : La vision d'ensemble du logiciel
📄 **Fichier :** [`README.md`](file:///opt/bldp/README.md)
* **Ce qu'il faut y comprendre :**
  * Ce que fait chaque commande du CLI (`pipeline`, `ingest`, `analyze`, `extract`, `validate`, `suivi`).
  * Les formats d'entrée (PDF natifs vs scannés) et les formats de sortie (SQLite `legal_database.sqlite`, `documents.jsonl`, `articles.jsonl`).
  * La section *« Collecter le corpus »* : comment se découpent les tranches de collecte depuis le Secrétariat Général du Gouvernement (SGG).

### Étape 2 : Les règles juridiques et éthiques non négociables
📄 **Fichier :** [`Cahier des charges — Benin Legal Data Pipeline.md`](file:///opt/bldp/Cahier%20des%20charges%20—%20Benin%20Legal%20Data%20Pipeline.md)
* **Ce qu'il faut y comprendre :**
  * **§0 & §33** : Pourquoi la traçabilité est absolue. Le texte original ne doit jamais être dénaturé par des corrections orthographiques ou des suppressions hasardeuses.
  * **§16** : Pourquoi une IA ne valide jamais un texte juridique. Elle propose, signale, classe en `a_verifier`, mais la décision `valide` porte obligatoirement une signature humaine.
  * **Confidentialité** : Aucun document ne quitte les machines sans consentement (`privacy.allow_external_calls: false`).

### Étape 3 : La réalité du terrain et le catalogue des pannes
📄 **Fichier :** [`docs/RELAIS.md`](file:///opt/bldp/docs/RELAIS.md) *(ou `/opt/bldp-exploitation/RELAIS.md`)*
* **C'est le document le plus complet et le plus important du projet.**
* **Ce qu'il faut y comprendre :**
  * **§0 (Invariants)** : `input/` n'est jamais modifié ; rien n'est effacé sans preuve d'empreinte ; une seconde entre deux requêtes au SGG ; adresse `dikdokmoney@gmail.com` dans le `User-Agent`.
  * **§1 (État du corpus)** : Le découpage en lots (Lot 1 à 6 terminés, Lot 7 en cours, environ 1 300 pages d'index au total).
  * **§4 (Le carnet pas à pas)** : Le rôle de chaque cellule Jupyter exécutée sur Colab.
  * **§5 & §6 (Dimensionnement)** : Pourquoi 2 fils CPU et pas de GPU ; pourquoi borner les tranches à 150 pages (~2 700 documents et ~4 Go) pour ne pas saturer le disque du VPS ni dépasser les 12 h de Colab.
  * **§7 (Ce qui a déjà échoué)** : Chaque incident réel documenté avec sa cause racine (mises en veille, timeouts, clés SSH aplaties, modules fantômes).

### Étape 4 : Le rôle spécifique du téléphone
📄 **Fichier :** [`docs/TERMUX.md`](file:///opt/bldp/docs/TERMUX.md) *(et [`AGENT_TERMUX.md`](file:///opt/bldp-exploitation/termux/AGENT_TERMUX.md))*
* **Ce qu'il faut y comprendre :**
  * Pourquoi le téléphone doit initier le transfert (le VPS ne peut pas joindre un téléphone derrière NAT 4G).
  * Le fonctionnement mécanique du reçu : `bldp-sync.sh` constate $\rightarrow$ dépose `/opt/bldp/recu/<lot>.ok` $\rightarrow$ `liberer.sh` efface côté VPS uniquement si le reçu concorde.
  * L'interdiction absolue du drapeau `--delete` dans `rsync`.

---

## 4. Lexique des termes du projet

* **SGG** : Secrétariat Général du Gouvernement du Bénin (`sgg.gouv.bj`), la source officielle primaire des décrets et lois.
* **LCF** : *Legal Crawler Framework*, l'outil d'indexation dont les schémas de métadonnées servent de référence de confrontation.
* **Tranche / Lot** : Bloc de 150 pages d'index du SGG (représentant environ 2 500 à 2 900 décrets).
* **Manifeste** : Fichier JSON inclus dans chaque archive associant à chaque document son URL source, son empreinte SHA-256 et son historique.
* **Reçu (`.ok`)** : Fichier texte déposé sur le serveur par le téléphone attestant du nombre exact de fichiers reçus intacts et de l'empreinte de l'archive.
* **Sidecar OCR** : Fichier PDF intermédiaire généré par OCRmyPDF lorsque le document d'origine est un scan non textuel.

---

## 5. Règle de décision pour l'agent local

Lorsque vous intervenez localement, appliquez toujours cet algorithme :

```text
Y a-t-il une ambiguïté ou un doute sur l'intégrité d'un fichier ?
    ├── OUI ──→ S'arrêter immédiatement, consigner l'anomalie, alerter l'humain.
    └── NON ──→ Procéder étape par étape avec vérification systématique de l'empreinte SHA-256.
```
