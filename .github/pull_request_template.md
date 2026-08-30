## Objet

<!-- Le problème d'abord, la solution ensuite. -->

## Type de changement

- [ ] Correction de bug
- [ ] Nouvelle fonctionnalité
- [ ] Documentation
- [ ] Nouvelle juridiction

## Impact sur les corpus existants

- [ ] Ce changement **peut modifier** le contenu extrait d'un corpus déjà traité
- [ ] Ce changement est sans effet sur les sorties existantes

<!-- Si la première case est cochée, décrivez précisément ce qui change. -->

## Vérifications

- [ ] `python -m pytest` passe intégralement
- [ ] Les nouvelles règles touchant au texte sont couvertes par un test de
      non-régression prouvant qu'aucun contenu juridique n'est perdu
- [ ] Aucun paramètre métier n'a été codé en dur (tout passe par la config)
- [ ] Aucun appel réseau n'a été ajouté sans être désactivé par défaut
- [ ] Aucun document juridique réel n'a été ajouté au dépôt
