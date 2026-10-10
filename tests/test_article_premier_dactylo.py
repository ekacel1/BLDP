"""L'article premier et l'en-tête des textes dactylographiés.

Cas réels de la tranche T002 du lot 1 (ordonnances de 1963 à 1977) :
« ARTICLE Ier », lu « ler », « Icr » ; « Artiole » ; le « O » décoratif de
« ORDONNE », lu « (二) » ou perdu.
"""
from bldp.core.cleaning.normalizer import apply_ocr_fixes, restore_decorative_o


def test_ier_majuscules():
    assert apply_ocr_fixes("O R D O N N E\nARTICLE Ier.- L'article 250")[0] == "O R D O N N E\nARTICLE 1er.- L'article 250"


def test_ler_et_icr():
    assert apply_ocr_fixes("ORDONNE\nArticle ler.- Est interdite")[0] == "ORDONNE\nArticle 1er.- Est interdite"
    assert apply_ocr_fixes("ARTICLE Icr : Le Ministre")[0] == "ARTICLE 1er : Le Ministre"
    assert apply_ocr_fixes("ARTICLE_Ier : Le Ministre")[0] == "ARTICLE 1er : Le Ministre"


def test_rien_d_autre_ne_bouge():
    for texte in ("ARTICLE II.- Les dépenses", "Article 1er.- Est autorisée", "Article Iles", "l'article Ier de la loi"):
        assert apply_ocr_fixes(texte)[0] == texte


def test_artiole():
    assert apply_ocr_fixes("Artiole 23 : Le Parti")[0] == "Article 23 : Le Parti"
    assert apply_ocr_fixes("ARTIOLE 5.- La loi")[0] == "ARTICLE 5.- La loi"
    assert apply_ocr_fixes("Artiole ler.- Le Bénin")[0] == "Article 1er.- Le Bénin"


def test_o_decoratif_de_ordonne():
    assert restore_decorative_o("Le Conseil des Ministres entendu,\n(二) R D O N N E :")[0] == \
        "Le Conseil des Ministres entendu,\nO R D O N N E :"
    assert restore_decorative_o("(二)RDONNANCE N° 1/PR/HCPT")[0] == "ORDONNANCE N° 1/PR/HCPT"
    assert restore_decorative_o("RDONNANCE N° 53 /PR/MEF")[0] == "ORDONNANCE N° 53 /PR/MEF"
    assert restore_decorative_o("(_) R D O N N A N C E")[0] == "O R D O N N A N C E"
    # de bout en bout, dans le nettoyage OCR
    assert apply_ocr_fixes("entendu,\n(二) R D O N N E :\nARTICLE Ier.- Est")[0] == \
        "entendu,\nO R D O N N E :\nARTICLE 1er.- Est"


def test_o_decoratif_rien_d_autre():
    for texte in ("O R D O N N E :", "ORDONNANCE N° 12", "la présente ordonnance", "BORDONNE", "un (二) seul"):
        assert restore_decorative_o(texte) == (texte, 0)


def test_o_decoratif_autres_ornements():
    assert restore_decorative_o("(0) R D O N N E :")[0] == "O R D O N N E :"
    assert restore_decorative_o("(□)RDONNANCE N° 48")[0] == "ORDONNANCE N° 48"
    assert restore_decorative_o("(C)RDONNANCE N° 27")[0] == "ORDONNANCE N° 27"
    assert restore_decorative_o("(O) R D O N N E")[0] == "O R D O N N E"
