"""L'article premier des textes dactylographiés : « ARTICLE Ier », lu « ler », « Icr ».

Cas réels de la tranche T002 du lot 1 (ordonnances de 1963 à 1968).
"""
from bldp.core.cleaning.normalizer import apply_ocr_fixes


def test_ier_majuscules():
    assert apply_ocr_fixes("O R D O N N E\nARTICLE Ier.- L'article 250")[0] == "O R D O N N E\nARTICLE 1er.- L'article 250"


def test_ler_et_icr():
    assert apply_ocr_fixes("ORDONNE\nArticle ler.- Est interdite")[0] == "ORDONNE\nArticle 1er.- Est interdite"
    assert apply_ocr_fixes("ARTICLE Icr : Le Ministre")[0] == "ARTICLE 1er : Le Ministre"
    assert apply_ocr_fixes("ARTICLE_Ier : Le Ministre")[0] == "ARTICLE 1er : Le Ministre"


def test_rien_d_autre_ne_bouge():
    for texte in ("ARTICLE II.- Les dépenses", "Article 1er.- Est autorisée", "Article Iles", "l'article Ier de la loi"):
        assert apply_ocr_fixes(texte)[0] == texte
