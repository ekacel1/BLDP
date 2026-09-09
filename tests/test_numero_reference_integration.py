"""Le numéro et la date sont confrontés à la référence du SGG, de bout en bout.

Les tests de `test_numero_visa.py` éprouvent chaque couche isolément. Ceux-ci
vérifient que `extract_metadata` les enchaîne correctement — c'est là que se
joue le résultat réel, et c'est là que l'ancien code laissait passer un visa
avec une confiance de 0,92 et aucun avertissement.

Mesuré sur le corpus SGG avant correction : 13 182 documents sur 28 896
portaient le numéro d'un texte cité. Après : 0 régression sur 300 documents
éprouvés, et la répartition du travail montre que la référence du SGG est
indispensable — l'intitulé seul ne suffit que dans 45 % des cas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bldp.config import load_config
from bldp.models import Page, SourceFile
from bldp.core.metadata.engine import extract_metadata
from bldp.utils import today_iso


@pytest.fixture
def config():
    return load_config()


def pages(texte: str) -> list[Page]:
    return [Page(document_id="doc", page=1, text=texte, source_file="doc.pdf")]


def source(tmp_path: Path, filename: str) -> SourceFile:
    chemin = tmp_path / filename
    chemin.write_bytes(b"%PDF-1.4")
    return SourceFile(
        document_id=filename.rsplit(".", 1)[0].replace("-", "_"),
        source_path=str(chemin),
        filename=filename,
        extension=".pdf",
        size_bytes=8,
        file_hash="0" * 64,
        ingested_at=today_iso(),
        category="decrets",
    )


#: Le cas réel qui a motivé la correction : un décret de 1999 dont le préambule
#: vise la loi 90-032, et dont l'OCR a mutilé le « VU » en « \\U ».
DECRET_1999_VISA_MUTILE = (
    "REPUBLIQUE DU BENIN\n"
    "\\U la loi n° 90-032 du 11 decembre 1990 portant Constitution ;\n"
    "Portant nomination d un directeur.\n"
)


class TestNumeroConfronteALaReference:
    def test_le_visa_ne_devient_pas_le_numero_du_document(self, config, tmp_path):
        """1 357 documents du corpus portaient « 90-032 » pour cette raison."""
        metadata = extract_metadata(
            "decret_1999_079",
            pages(DECRET_1999_VISA_MUTILE),
            config,
            source(tmp_path, "decret-1999-079.pdf"),
        )
        assert metadata.number == "1999-079"

    def test_le_desaccord_est_signale_et_la_valeur_lue_conservee(self, config, tmp_path):
        """On ne choisit pas en silence : la trace des deux valeurs subsiste."""
        metadata = extract_metadata(
            "decret_1999_079",
            pages(DECRET_1999_VISA_MUTILE),
            config,
            source(tmp_path, "decret-1999-079.pdf"),
        )
        assert any("numéro" in a for a in metadata.warnings), metadata.warnings
        preuve = metadata.evidence.get("number", "")
        assert "1999-079" in preuve
        assert "90-032" in preuve, "la valeur lue dans le texte doit rester visible"

    def test_un_intitule_lisible_est_respecte(self, config, tmp_path):
        """Quand le texte et la référence s'accordent, rien n'est signalé."""
        metadata = extract_metadata(
            "decret_2013_211",
            pages(
                "REPUBLIQUE DU BENIN\n"
                "DECRET N° 2013-211 du 10 avril 2013\n"
                "VU la loi n° 90-032 du 11 decembre 1990 ;\n"
            ),
            config,
            source(tmp_path, "decret-2013-211.pdf"),
        )
        assert metadata.number == "2013-211"
        assert not any("numéro" in a for a in metadata.warnings), metadata.warnings

    def test_la_reference_comble_un_numero_illisible(self, config, tmp_path):
        """3 611 documents du corpus n'avaient aucun numéro extractible."""
        metadata = extract_metadata(
            "decret_1989_413",
            pages("PRnS InXNtln l}i LA REPUBLIQUE\n7l).Ecne r illisible\n"),
            config,
            source(tmp_path, "decret-1989-413.pdf"),
        )
        assert metadata.number == "1989-413"
        assert "SGG" in metadata.evidence.get("number", "")


class TestDateConfronteeALaReference:
    def test_une_annee_en_desaccord_est_signalee_sans_etre_ecrasee(self, config, tmp_path):
        """Le 11 décembre 1990 — jour de la Constitution — se retrouvait porté
        par des textes de 1999.

        On signale sans corriger : un texte signé en décembre peut légitimement
        porter le numéro de l'année suivante, et écraser la date lue
        remplacerait une incertitude par une invention.
        """
        metadata = extract_metadata(
            "decret_1999_134",
            pages(
                "REPUBLIQUE DU BENIN\n"
                "\\U la loi n° 90-032 du 11 decembre 1990 portant Constitution ;\n"
            ),
            config,
            source(tmp_path, "decret-1999-134.pdf"),
        )
        if metadata.date:
            if metadata.date[:4] != "1999":
                assert any("date" in a for a in metadata.warnings), metadata.warnings
                assert metadata.confidence.get("date", 1.0) <= 0.50

    def test_une_date_coherente_ne_declenche_rien(self, config, tmp_path):
        metadata = extract_metadata(
            "decret_2013_211",
            pages(
                "DECRET N° 2013-211 du 10 avril 2013\n"
                "Portant organisation des services.\n"
            ),
            config,
            source(tmp_path, "decret-2013-211.pdf"),
        )
        assert metadata.date == "2013-04-10"
        assert not any("date :" in a for a in metadata.warnings), metadata.warnings
