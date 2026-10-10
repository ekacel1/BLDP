"""L'intitulé des actes anciens sans « portant … » (lot 1, tranche T002, ordonnances 1963-1968)."""
from bldp.core.metadata.engine import detect_title
from bldp.models import DocumentType

ORD = DocumentType.ORDONNANCE


def test_en_tete_sur_une_ligne_plutot_que_la_devise():
    texte = "REPUBLIQUE DU DAHOMEY\nGOUVERNEMENT PROVISOIRE\nO R D O N N A N C E N° 12/GPRD\nLE PRESIDENT DU GOUVERNEMENT"
    assert detect_title(texte, ORD, None)[0] == "ORDONNANCE N° 12/GPRD"


def test_en_tete_sur_deux_lignes():
    texte = ("REPUBLIQUE DU DAHOMEY\nPRESIDENCE DE LA REPUBLIQUE\nO R D O N N A N C E\nANNEE 1966 - N° 48 /PR/MFAE\n"
             "LE PRESIDENT DE LA REPUBLIQUE\nVU le Décret N°215/PR du 16 Mai 1966, déterminant les Services")
    assert detect_title(texte, ORD, None)[0] == "ORDONNANCE ANNEE 1966 - N° 48 /PR/MFAE"


def test_un_visa_n_est_jamais_l_intitule():
    texte = ("ORDONNANCE N° 18/GPRD/SGG\nLE PRESIDENT DU GOUVERNEMENT PROVISOIRE\n"
             "VU l'Ordonnance n° 1/GPRD du 28 Octobre 1963 portant suppression d'institutions\nORDONNE :")
    assert detect_title(texte, ORD, None)[0] == "ORDONNANCE N° 18/GPRD/SGG"


def test_rapportant_est_un_objet():
    texte = "ORDONNANCE N° 19/GPRD\nrapportant l'Ordonnance n° 8/GPRD du 8 Novembre 1963\nVU la Constitution"
    assert detect_title(texte, ORD, None)[0].startswith("rapportant l'Ordonnance n° 8/GPRD")


def test_un_texte_moderne_ne_change_pas():
    texte = "DÉCRET n° 2019-230 DU 31 JUILLET 2019 portant nomination des commissaires aux comptes\nLE PRESIDENT"
    titre, confiance, _ = detect_title(texte, DocumentType.DECRET, "2019-230")
    assert titre.startswith("DÉCRET n° 2019-230 DU 31 JUILLET 2019 portant nomination") and confiance == 0.85
