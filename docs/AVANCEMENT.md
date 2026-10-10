# Avancement de BLDP

_Mis à jour automatiquement le 2026-10-10 02:27 UTC par `exploitation/avancement.py` — ne pas éditer à la main._

## Méthode en cours

Le lot1 est relu en entier par **PaddleOCR-VL** (Colab, GPU), par tranches de 50 documents, au rythme de 100 documents par jour. Une tranche ne mélange jamais deux catégories, sauf les plus petites, regroupées ; chaque document garde sa catégorie. Après chaque tranche :

1. le pipeline ouvre un **ticket** (registre : `a_verifier`) ;
2. **Gemini** vérifie chaque document contre la photo de ses pages, corrige ce qu'il sait corriger exactement, et valide le ticket ;
3. sa réponse est contrôlée, puis les données vérifiées rejoignent la **base propre** du lot (registre : `revue_ia`). Les cas douteux attendent un humain (`en_revue`).

La validation définitive reste humaine (§16) : `valide` n'est jamais posé par une IA.

**Prochaine action :** lecture de T003 en cours sur Colab.

## lot1 — par catégorie

| Catégorie | Documents | Lus | Vérifiés par Gemini | Versés (base propre) | À revoir (humain) |
|---|---:|---:|---:|---:|---:|
| arrete | 2 | 2 | 2 | 2 | 0 |
| decision | 2 | 2 | 2 | 1 | 1 |
| accord | 12 | 12 | 12 | 6 | 6 |
| ordonnance | 977 | 50 | 50 | 24 | 26 |
| loi | 1529 | 0 | 0 | 0 | 0 |
| **total** | **2522** | **66** | **66** | **33** | **33** |

## Tranches (2/52 intégrées)

| Tranche | Catégories | Docs | Pages | Statut | Lancée | Ticket | Vérifiée par | Intégrée | Conformes | Corrigés | À revoir |
|---|---|---:|---:|---|---|---|---|---|---:|---:|---:|
| T001 | arrete 2, decision 2, accord 12 | 16 | 85 | INTEGREE | 2026-10-05 | 2026-10-05 | gemini (gemini-3.8-flash) | 2026-10-05 | — | 9 | 7 |
| T002 | ordonnance 50 | 50 | 126 | INTEGREE | 2026-10-05 | 2026-10-06 | claude (claude-opus-5-5), 7 vérificateurs en parallèle, à la demande de l'utilisateur | 2026-10-10 | 1 | 23 | 26 |
| T003 | ordonnance 50 | 50 | — | EN_COURS | 2026-10-10 | — | — | — | — | — | — |
| T004 | ordonnance 50 | 50 | — | ECHEC | 2026-10-10 | — | — | — | — | — | — |
| T005 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T006 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T007 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T008 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T009 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T010 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T011 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T012 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T013 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T014 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T015 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T016 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T017 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T018 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T019 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T020 | ordonnance 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T021 | ordonnance 27 | 27 | — | A_LANCER | — | — | — | — | — | — | — |
| T022 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T023 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T024 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T025 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T026 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T027 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T028 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T029 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T030 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T031 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T032 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T033 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T034 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T035 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T036 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T037 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T038 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T039 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T040 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T041 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T042 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T043 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T044 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T045 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T046 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T047 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T048 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T049 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T050 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T051 | loi 50 | 50 | — | A_LANCER | — | — | — | — | — | — | — |
| T052 | loi 29 | 29 | — | A_LANCER | — | — | — | — | — | — | — |

## Registre de suivi (base propre du lot)

| Étape | Documents |
|---|---:|
| en_revue | 26 |
| revue_ia | 40 |
