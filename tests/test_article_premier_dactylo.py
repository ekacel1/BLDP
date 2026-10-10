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


def test_un_tape_i_dans_les_dates():
    from bldp.core.cleaning.normalizer import fix_typewriter_one_in_dates as f
    assert f("COTONOU, le I3 Décembre I963.")[0] == "COTONOU, le 13 Décembre 1963."
    assert f("COTONOU, le 3I Décembre 1963")[0] == "COTONOU, le 31 Décembre 1963"
    assert f("la loi n°65-3I du I4 Août I965")[0] == "la loi n°65-3I du 14 Août 1965"
    assert f("COTONOU, le I4 JANVIER 1964")[0] == "COTONOU, le 14 JANVIER 1964"


def test_un_tape_i_rien_d_autre():
    from bldp.core.cleaning.normalizer import fix_typewriter_one_in_dates as f
    for texte in ("le 1er Janvier 1964", "TITRE II", "Chapitre III mars", "le 12 mars 2019"):
        assert f(texte) == (texte, 0)


def test_la_date_de_signature_redevient_lisible():
    from bldp.core.metadata.engine import detect_date
    from bldp.jurisdictions.registry import get_jurisdiction
    texte = apply_ocr_fixes("ORDONNANCE N° 19/GPRD\nVU l'ordonnance du 8 Novembre 1963\nCOTONOU, le I3 Décembre I963.")[0]
    assert detect_date(texte, get_jurisdiction("benin"), None, texte)[0] == "1963-12-13"


def test_le_lu_1e_devant_une_date():
    from bldp.core.cleaning.normalizer import fix_typewriter_one_in_dates as f
    assert f("COTONOU, 1e I3 Décembre 1963.")[0] == "COTONOU, le 13 Décembre 1963."
    assert f("Article 1e 3 mars")[0] == "Article 1e 3 mars"


def test_variantes_t003():
    assert apply_ocr_fixes("Articlo 42.- Sera puni")[0] == "Article 42.- Sera puni"
    assert apply_ocr_fixes("Articolo 64.- Les")[0] == "Article 64.- Les"
    assert apply_ocr_fixes("fin.\nA article 52.- Le juge")[0] == "fin.\nArticle 52.- Le juge"
    assert apply_ocr_fixes("ORDONNE\n_ticle ler.- Est")[0] == "ORDONNE\nArticle 1er.- Est"
    assert apply_ocr_fixes("à article 52 de la loi")[0] == "à article 52 de la loi"


def test_variantes_t004():
    assert apply_ocr_fixes("ARSIOLE 2.- Les Hautes Parties")[0] == "ARTICLE 2.- Les Hautes Parties"
    assert apply_ocr_fixes("ARTICLE 1er!- Sont ouverts")[0] == "ARTICLE 1er.- Sont ouverts"
    assert apply_ocr_fixes("Attention!- fin")[0] == "Attention!- fin"
