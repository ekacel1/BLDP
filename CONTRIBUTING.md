# Contribuer à BLDP

Merci de votre intérêt. Ce projet construit une base de données juridique qui
servira à d'autres systèmes : une erreur d'extraction peut entraîner une
mauvaise interprétation du droit. Les règles ci-dessous en découlent.

## La règle qui prime sur toutes les autres

> **Exactitude > automatisation.**

Toute contribution qui touche à l'extraction, au nettoyage ou au parsing doit
être accompagnée d'un test prouvant qu'aucun contenu juridique n'est perdu ou
altéré. Une règle de nettoyage sans test de non-régression sera refusée, même
si elle « marche visiblement ».

En cas de doute, le code doit **signaler**, jamais deviner.

## Mise en route

```bash
git clone https://github.com/OWNER/bldp.git
cd bldp
python -m pip install -e ".[dev]"
python -m bldp doctor
python -m pytest
```

## Ce qui est attendu d'une contribution

### Tests

- La suite complète doit passer : `python -m pytest`.
- Toute correction de bug commence par un test qui échoue.
- Les tests nécessitant l'OCR ou les embeddings portent
  `@pytest.mark.requires_ocr` ou `@pytest.mark.requires_embeddings` : la suite
  doit rester exécutable sur une machine nue.
- N'ajoutez **aucun document juridique réel** au dépôt. Les PDF de test sont
  fabriqués à la volée dans `tests/conftest.py`.

### Style

- Python 3.11+, annotations de types sur les fonctions publiques.
- Docstrings en français, au format Google, expliquant le *pourquoi* d'une règle
  métier plutôt que le *quoi* du code.
- Les commentaires justifient les décisions non évidentes — en particulier les
  garde-fous protégeant le contenu juridique.
- Aucun paramètre métier codé en dur : tout passe par `config/default.yaml`.

### Architecture

- `bldp/core/` doit rester **générique**. Rien de spécifique à un pays n'y entre.
- Les règles nationales vivent dans `bldp/jurisdictions/<pays>/rules.py`.
- Les dépendances lourdes (OCR, embeddings, FAISS, web) restent **optionnelles**
  et importées paresseusement.
- Aucun appel réseau ne doit être ajouté sans être explicitement configurable et
  désactivé par défaut (§27 du cahier des charges).

## Ajouter une juridiction

1. Créez `bldp/jurisdictions/<pays>/` avec `__init__.py` et `rules.py`.
2. `rules.py` expose `build() -> JurisdictionProfile`.
3. Ne redéclarez pas ce que le socle générique sait déjà : le registre fusionne
   automatiquement vos règles par-dessus.
4. Ajoutez des tests avec des exemples représentatifs (fictifs).

## Ajouter une règle de nettoyage

Le nettoyage est la partie la plus risquée du projet. Une nouvelle règle doit :

1. être désactivable par configuration ;
2. respecter le veto de `is_protected()` — une ligne portant un marqueur
   juridique n'est jamais supprimée ;
3. venir avec un test montrant qu'elle retire bien l'artefact visé, **et** un
   test montrant qu'elle ne touche pas à un contenu juridique voisin ;
4. être comptabilisée dans `CleaningReport`, pour rester auditable.

## Signaler un bug

Indiquez :

- la commande exacte et la version (`python -m bldp --version`) ;
- la sortie de `python -m bldp doctor` ;
- un extrait **anonymisé** ou un PDF reproduisant le problème ;
- le comportement attendu et le comportement observé.

Un bug d'extraction silencieux — du texte perdu sans avertissement — est
considéré comme prioritaire.

## Pull requests

- Une PR par sujet.
- Décrivez le problème avant la solution.
- Indiquez explicitement si votre changement peut modifier le contenu extrait
  d'un corpus existant : c'est une information critique pour les utilisateurs.
