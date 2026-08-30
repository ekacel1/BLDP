# Architecture

Ce document explique **pourquoi** le code est organisé ainsi. Pour le *quoi*,
lisez les docstrings : chaque module ouvre sur son rôle et ses garde-fous.

## Le principe qui gouverne tout

> **Exactitude > automatisation.** En cas de doute : ne pas deviner → signaler
> → validation humaine.

Ce n'est pas une devise décorative. Il se traduit par des décisions concrètes,
répétées à chaque étage :

| Situation | Ce que fait BLDP | Ce qu'il ne fait **pas** |
|---|---|---|
| Une ligne pourrait être un en-tête répété **et** un article | conserve la ligne | supprime au bénéfice du doute |
| Numérotation `1, 3` | signale « article 2 manquant » | renumérote ou complète |
| Aucune date trouvée | laisse le champ vide + avertit | déduit une date du nom de fichier |
| Statut juridique inconnu | `inconnu` | suppose `en_vigueur` |
| Deux fichiers identiques | marque le doublon | supprime l'un des deux |
| OCR nécessaire mais indisponible | extrait le natif + **erreur** explicite | produit un corpus amputé sans le dire |
| Contrôle qualité sans anomalie | `en_attente` | `valide` |

## Les trois couches

```text
┌───────────────────────────────────────────────────────┐
│  bldp/cli.py, bldp/web/       interfaces               │
├───────────────────────────────────────────────────────┤
│  bldp/pipeline.py             orchestration            │
├───────────────────────────────────────────────────────┤
│  bldp/core/                   traitement générique     │
│  bldp/jurisdictions/          règles nationales        │
└───────────────────────────────────────────────────────┘
```

**Règle de dépendance** : `core/` ne remonte jamais vers `pipeline` ni vers les
interfaces, et ne connaît aucune juridiction. Les modules de `core/` sont
utilisables isolément — c'est ce qui rend chacun testable sans monter tout le
pipeline.

## Séparation cœur / juridiction (§29)

Le cœur ignore tout du Bénin. Le registre charge un profil et **fusionne** ses
règles par-dessus le socle générique :

```python
profile.ruleset = generic_ruleset().extend(profile.ruleset)
```

Une juridiction n'a donc jamais à redéclarer « Chapitre » ou « Article ». Elle
n'apporte que ses particularités : `Article unique`, `Article 45 nouveau`,
« Fait à Cotonou », le format `AAAA-NNN` des numéros officiels.

Une juridiction inconnue retombe sur le socle générique **avec un
avertissement**, plutôt que de bloquer le pipeline.

## Le modèle de données porte la traçabilité

`bldp/models.py` matérialise la chaîne exigée au §33 :

```text
SourceFile  → le fichier d'origine, son empreinte, son chemin
   Page     → texte brut ET texte nettoyé, numéro de page, méthode d'extraction
   Article  → texte, alinéas, contexte hiérarchique, page de début et de fin
   Metadata → chaque champ + son score de confiance + sa preuve
   Relation → citation brute conservée même si la cible n'est pas résolue
```

Deux choix méritent d'être soulignés.

**`Page.raw_text` et `Page.text` coexistent.** Le nettoyage n'est jamais
destructif : on peut toujours comparer avant/après, rejouer avec d'autres
règles, ou contester une suppression. C'est ce qui rend la vue de validation à
trois couches possible.

**`DocumentMetadata.confidence` et `.evidence`.** Toute valeur déduite sait d'où
elle vient. Le pipeline peut répondre à « d'où vient cette date ? » par
`"du 10 fevrier 2026"` et un score de 0,95. Sans cela, un corpus juridique n'est
pas auditable.

## Décisions de conception notables

### Le classifieur produit une confiance, pas un booléen

`ocr_required: true` ne suffit pas. La confiance **s'effondre au voisinage des
seuils** — précisément là où un humain doit trancher :

```python
margin = min(ratio_margin, alpha_margin)
base = 0.60 + 0.38 * margin
```

Un document à 61 % de pages textuelles (seuil : 60 %) obtient une confiance
inférieure à 0,70 et se voit marqué « vérification humaine recommandée ».

### Le nettoyage a un veto, pas une liste de règles

`is_protected()` est consulté **avant** toute règle de suppression. Une ligne
portant un marqueur juridique — article, alinéa, référence, date, montant,
sanction, exception, condition — est intouchable, même si elle se répète sur
toutes les pages.

Les zones d'en-tête et de pied ne se recouvrent jamais (`effective_zone`) :
sans cela, sur une page courte, le corps du texte tombait dans les deux zones et
pouvait être supprimé comme en-tête répété. Ce bug a bien existé ; le test
`test_zones_never_overlap_on_short_pages` le verrouille.

### Le parser borne les articles par les en-têtes suivants

Un article court de son en-tête jusqu'au **prochain en-tête reconnu**, quel que
soit son niveau. Conséquence assumée : si un en-tête est manqué, deux articles
fusionnent — mais la longueur anormale est détectée par le contrôle qualité et
signalée, plutôt que de couper au jugé.

Le texte antérieur au premier article devient le **préambule** : rien n'est jeté.

### Le type de document est déterminé par la correspondance la plus précoce

Un décret « portant application de la loi n° 2026-001 » contient le motif d'une
loi. Or l'intitulé d'un texte précède ses citations : c'est donc la
correspondance qui apparaît **le plus tôt** dans le texte qui l'emporte, et non
l'ordre de déclaration des types. Sans cette règle, une simple référence
requalifiait le document — une erreur de qualification juridique.

### Les relations ne modifient un statut qu'à confiance suffisante

Trois barrières successives :

1. la référence doit être **normalisable** — « toutes dispositions antérieures
   contraires » ne vise personne et ne change rien ;
2. la cible doit être **résolue** dans le corpus ; sinon la citation est
   conservée avec `needs_review=True`, en attente du texte manquant ;
3. la confiance doit dépasser `relations.min_confidence` ; en dessous, on
   signale sans agir.

Toute modification de statut porte la mention « à confirmer par un juriste ».

### Le chunking suit le droit, pas une longueur

Priorité `Article → alinéas → phrases`. Un article qui tient reste **entier** :
c'est l'unité de citation naturelle du droit. Une tolérance de 20 % évite de
fragmenter un article pour quelques mots. On ne descend au niveau de la phrase
que pour un alinéa unique démesuré.

### Les dépendances lourdes sont optionnelles et paresseuses

OCR, embeddings, FAISS et web sont des extras. Leurs imports sont différés :
importer `bldp.core.embeddings` ne télécharge jamais un modèle. Chaque module
expose un `check_*_ready()` qui explique en clair ce qui manque.

L'indisponibilité d'un extra ne fait jamais échouer un corpus par ailleurs
complet — sauf pour l'OCR, où l'absence de texte est signalée comme une
**erreur**, car le corpus serait alors réellement amputé.

## Gestion des erreurs (§26)

Chaque document est traité dans son propre bloc protégé. Un échec est consigné,
compté, et **le document reste dans le corpus** avec son erreur — il ne
disparaît pas silencieusement.

Les trois compteurs sont mutuellement exclusifs : un document est réussi, à
vérifier, ou en échec. Jamais deux à la fois.

## Persistance

SQLite, 11 tables, cascades et index. Trois propriétés voulues :

- **idempotence** : réécrire un document remplace ses lignes filles de façon
  transactionnelle, sans accumulation ;
- **la décision humaine survit** au retraitement — le travail d'un relecteur
  n'est jamais effacé par une nouvelle exécution ;
- **`trace_article()`** remonte en une requête `article → document → page →
  fichier source`. C'est l'exigence d'auditabilité rendue exécutable.

## Ce que BLDP ne fait pas

- Aucune analyse ou interprétation juridique.
- Aucune correction automatique d'un contenu douteux.
- Aucun appel réseau tant que `privacy.allow_external_calls` vaut `false`.
- Aucune auto-validation : le statut `valide` ne peut venir que d'un humain.
