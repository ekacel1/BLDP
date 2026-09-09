"""Le numéro d'un texte est le sien, pas celui d'un texte qu'il cite.

Ces tests verrouillent une correction qui a coûté cher à découvrir. Sur le
corpus SGG, **13 182 documents sur 28 896 comparables — 45,6 %** portaient le
numéro d'un autre texte : 1 357 d'entre eux annonçaient « 90-032 », 818
« 77-032 ». Ce sont les numéros des textes les plus cités, capturés dans les
visas du préambule.

Le mécanisme : `CITATION_LINE_RE` exige que la ligne commence par « vu » ou
« considérant ». Sur les scans anciens, l'OCR rend « VU » en `\\U`, `JU`, `\\(]`,
`\\,u` ; le visa échappe au filtre, survit dans le texte examiné, et devient
indiscernable d'un intitulé.

Trois couches y répondent, et chacune est éprouvée ici :

* **B** — `ouvre_le_preambule` reconnaît le début des visas même mutilé ;
* **C** — `entete` borne l'intitulé avant le premier visa numéroté ;
* **D** — la référence du SGG, qui ne passe par aucun OCR, tranche les
  désaccords et le signale.
"""

from __future__ import annotations

import pytest

from bldp.core.metadata.engine import (
    entete,
    memes_numeros,
    numero_de_reference,
    ouvre_le_preambule,
)


# --------------------------------------------------------------- couche B --
@pytest.mark.parametrize(
    "ligne",
    [
        "VU la loi n° 90-032 du 11 décembre 1990 portant Constitution ;",
        "Vu le décret n° 77-032 du 5 mars 1977 ;",
        "CONSIDÉRANT la loi n° 2015-018 ;",
        # Mutilations relevées telles quelles dans le corpus, lots 11 et 12.
        "\\U }e nécret n\"60-12/PR du 2! Janvie t L964t portant formation ;",
        "\\,u Ie Décre+. n° 54-021/PC du 2 l\\[,ai ;",
    ],
)
def test_ouverture_de_visa_reconnue_meme_mutilee(ligne: str) -> None:
    assert ouvre_le_preambule(ligne)


@pytest.mark.parametrize(
    "ligne",
    [
        "DECRET N° 2013-211 du 10 avril 2013",
        "RÉPUBLIQUE DU DAHOMEY",
        "LE PRÉSIDENT DE LA RÉPUBLIQUE,",
        "Article 1er : Le présent décret entre en vigueur.",
    ],
)
def test_un_intitule_nest_pas_pris_pour_un_visa(ligne: str) -> None:
    """Amputer l'intitulé serait pire que le mal : on y perdrait le numéro."""
    assert not ouvre_le_preambule(ligne)


def test_un_visa_sans_numero_ne_borne_pas_lintitule() -> None:
    """Un visa qui ne cite aucun numéro ne peut pas en usurper un.

    Le borner serait une précaution inutile, et couperait l'intitulé plus tôt
    que nécessaire.
    """
    assert not ouvre_le_preambule("\\(] 1a Constitution du 11 Janvier L964 ;")
    assert not ouvre_le_preambule("JU les recours en grâce formés par les condamnés ;")


# --------------------------------------------------------------- couche C --
def test_entete_sarrete_au_premier_visa() -> None:
    texte = (
        "RÉPUBLIQUE DU BÉNIN\n"
        "DECRET N° 2013-211 du 10 avril 2013\n"
        "LE PRÉSIDENT DE LA RÉPUBLIQUE,\n"
        "VU la loi n° 90-032 du 11 décembre 1990 ;\n"
    )
    tete = entete(texte)
    assert "2013-211" in tete
    assert "90-032" not in tete


def test_entete_resiste_a_un_vu_mutile() -> None:
    """Le cas qui faisait échouer l'ancien filtre."""
    texte = (
        "REPUBLIQUE DU DAHOMEY\n"
        "Decret n° 1961-010\n"
        "\\U }e necret n\"60-12/PR du 2! Janvie t ;\n"
    )
    tete = entete(texte)
    assert "1961-010" in tete
    assert "60-12" not in tete


def test_entete_vide_si_le_visa_ouvre_le_document() -> None:
    """Sans intitulé lisible, mieux vaut ne rien retenir que retenir un visa."""
    texte = "VU le décret n° 90-032 ;\nVU la loi n° 64-054 ;\n"
    assert entete(texte).strip() == ""


# --------------------------------------------------------------- couche D --
@pytest.mark.parametrize(
    "reference, attendu",
    [
        ("decret-2012-465.pdf", "2012-465"),
        ("decret_2012_465", "2012-465"),
        ("loi-1961-10.pdf", "1961-010"),
        ("decret-60-36.pdf", "60-036"),
        ("arrete_2018_1", "2018-001"),
    ],
)
def test_numero_deduit_de_la_reference_sgg(reference: str, attendu: str) -> None:
    """L'URL du SGG porte le numéro sans passer par l'OCR."""
    assert numero_de_reference(reference) == attendu


def test_numero_de_reference_ignore_ce_qui_ne_sy_prete_pas() -> None:
    assert numero_de_reference(None) is None
    assert numero_de_reference("document-sans-numero.pdf") is None
    # Le premier candidat exploitable gagne.
    assert numero_de_reference(None, "sans-forme", "decret-2020-007") == "2020-007"


@pytest.mark.parametrize(
    "a, b",
    [
        ("61-037", "1961-037"),      # année sur deux ou quatre chiffres
        ("1961-37", "61-037"),       # série non cadrée
        ("2018-001/PR/SGG", "2018-001"),   # suffixe administratif
        ("2018 - 001", "2018-001"),  # espaces de l'OCR
    ],
)
def test_memes_numeros_ignore_la_notation(a: str, b: str) -> None:
    """Sans ce cadrage, 6 138 différences de forme passaient pour des erreurs."""
    assert memes_numeros(a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("60-12", "1961-010"),       # le cas réel : loi de 1961, numéro de 1960
        ("90-032", "2012-465"),      # la grappe de 1 357 documents
        ("2017-522", "2022-324"),
    ],
)
def test_memes_numeros_distingue_le_fond(a: str, b: str) -> None:
    assert not memes_numeros(a, b)


def test_memes_numeros_refuse_les_valeurs_vides() -> None:
    """Deux absences ne font pas un accord."""
    assert not memes_numeros(None, None)
    assert not memes_numeros("", "2013-211")
    assert not memes_numeros("2013-211", None)
