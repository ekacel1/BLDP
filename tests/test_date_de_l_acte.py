"""La date de l'acte : intitulé, puis signature, jamais une date d'effet.

Cas réels de l'essai « trois lectures » (5 octobre 2026), texte lu par
PaddleOCR-VL :

* ordonnance 93-04/PCS-CAB de la Cour suprême : l'intitulé porte la date à la
  ligne suivante, en chiffres espacés (« du » / « 10 - 03 - 1993 ») ; la
  signature dit « Cotonou, le 10 Mars 1993 » ; l'article 1er contient « à
  compter du 22 Mars 1993 ». Le pipeline retenait le 22 mars.
* ordonnance n° 53/PR/MEF de 1968 : pas de date dans l'intitulé, signature
  « Fait à COTONOU, le 2 Novembre 1968 ».
"""

from __future__ import annotations

import pytest

from bldp.core.crawl.lcf import date_retrouvee_dans_le_document
from bldp.core.metadata.engine import detect_date
from bldp.jurisdictions.registry import get_jurisdiction


@pytest.fixture
def benin():
    return get_jurisdiction("benin")


ENTETE_1993 = (
    "REPUBLIQUE DU BENIN\nCOUR SUPREME\nCABINET DU PRESIDENT\n"
    "ORDONNANCE N° 93-04/PCS-CAB du\n10 - 03 - 1993\n"
    "PORTANT REMISE A DISPOSITION DE SON BATAILLON D'ORIGINE DU SOLDAT DE 1ère CLASSE FASSINOU VALENTIN\n"
    "LE PRESIDENT DE LA COUR SUPREME\n"
    "VU La Constitution de la République du Bénin du 11 - 12 - 90;\n"
    "VU L'Ordonnance n° 92-01/PCS-CAB du 2 Janvier 1992 portant nomination de Monsieur FASSINOU Valentin\n"
    "ORDONNE\n"
    "ARTICLE 1er : Monsieur FASSINOU Valentin, Soldat de Première Classe, est remis à compter du "
    "22 Mars 1993 à la disposition de son bataillon d'origine.\n"
)
SIGNATURE_1993 = (
    "ARTICLE 2 : La Présente Ordonnance, qui abroge toutes dispositions contraires, sera publiée au "
    "Journal Officiel de la République du Bénin.\nCotonou, le 10 Mars 1993\nLE PRESIDENT DE LA COUR SUPREME\n"
    "F. N. HOUNDETON.-"
)


class TestLaDateDeLActe:
    def test_intitule_coupe_apres_du_date_en_chiffres_espaces(self, benin):
        iso, confiance, preuve = detect_date(ENTETE_1993, benin, "93-04/PCS-CAB", SIGNATURE_1993)
        assert iso == "1993-03-10"
        assert confiance >= 0.88

    def test_la_signature_passe_avant_une_date_d_effet(self, benin):
        """Sans la date de l'intitulé, c'est la signature — pas « à compter du 22 Mars »."""
        entete = ENTETE_1993.replace("du\n10 - 03 - 1993\n", "\n")
        iso, confiance, preuve = detect_date(entete, benin, "93-04/PCS-CAB", SIGNATURE_1993)
        assert iso == "1993-03-10"
        assert "signature" in preuve

    def test_une_date_d_effet_n_est_jamais_la_date_de_l_acte(self, benin):
        texte = "ORDONNANCE N° 93-04/PCS-CAB\nArticle 1er : est remis à compter du 22 Mars 1993 à la disposition."
        assert detect_date(texte, benin, "93-04/PCS-CAB")[0] is None

    def test_signature_fait_a_sans_date_dans_l_intitule(self, benin):
        entete = "RDONNANCE N° 53 /PR/MEF accordant à la Banque Dahoméenne de Développement le transfert"
        signature = "La présente ordonnance sera exécutée comme loi de l'Etat./- Fait à COTONOU, le 2 Novembre 1968"
        assert detect_date(entete, benin, "53/PR/MEF", signature)[0] == "1968-11-02"

    def test_l_intitule_reste_prioritaire_sur_la_signature(self, benin):
        entete = "DÉCRET N° 2017-499 du 18 octobre 2017 portant nomination au Ministère"
        signature = "Fait à Cotonou, le 19 octobre 2017"
        assert detect_date(entete, benin, "2017-499", signature)[0] == "2017-10-18"

    def test_une_ville_dans_le_texte_n_est_pas_une_signature(self, benin):
        texte = "le Président du Tribunal de Première Instance de Cotonou sollicitant l'autorisation de vendre"
        assert detect_date("ORDONNANCE N° 53 /PR/MEF", benin, "53/PR/MEF", texte)[0] is None


class TestLeTroisiemeTemoinLitLesChiffres:
    def test_date_chiffree_dans_l_intitule(self):
        page = ("ORDONNANCE N° 93-04/PCS-CAB du 10 - 03 - 1993 PORTANT REMISE A DISPOSITION "
                "LE PRESIDENT DE LA COUR SUPREME VU La Constitution")
        assert date_retrouvee_dans_le_document([page], "1993-03-10")

    def test_date_chiffree_abimee_par_l_ocr(self):
        assert date_retrouvee_dans_le_document(["ORDONNANCE N° 93-04 du lO - O3 - 1993 PORTANT"], "1993-03-10")

    def test_signature_sans_fait_a(self):
        pages = ["ORDONNANCE N° 93-04/PCS-CAB PORTANT REMISE. VU la loi",
                 "ARTICLE 2 : La Présente Ordonnance sera publiée. Cotonou, le 10 Mars 1993 LE PRESIDENT"]
        assert "10 Mars 1993" in date_retrouvee_dans_le_document(pages, "1993-03-10")

    def test_une_autre_date_chiffree_ne_prouve_rien(self):
        assert date_retrouvee_dans_le_document(["ORDONNANCE N° 93-04 du 18 - 03 - 1993 PORTANT"], "1993-03-10") is None
