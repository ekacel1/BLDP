"""Le texte inventé par un lecteur vision-langage est signalé (lot 1, tranche T002)."""
from bldp.core.validation.quality import lecture_inventee


def test_boucle_x_egal():
    texte = "ORDONNANCE N° 9/PR/MFAE " + " ".join(f"x{i}=-{i}" for i in range(1, 60))
    assert "boucle" in lecture_inventee(texte)


def test_boucle_a_deux_temps():
    assert "boucle" in lecture_inventee("ampliations " + " = 22" * 30)


def test_ecritures_etrangeres():
    assert "écriture étrangère" in lecture_inventee("signé : 李明 le Président")
    assert "écriture étrangère" in lecture_inventee("Article 44 ところで la loi")


def test_un_texte_sain_ou_un_tableau_ne_sont_pas_signales():
    assert lecture_inventee("ARTICLE 2.- La présente ordonnance sera exécutée comme loi de l'Etat.") is None
    assert lecture_inventee(" ".join(["1 000 000"] * 40)) is None
    assert lecture_inventee("de la loi de la République de la Nation " * 3) is None
