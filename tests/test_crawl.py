"""Tests de la lecture du catalogue de collecte (LCF) et de sa confrontation.

Les cas ne sont pas inventés : ils reprennent des fiches réelles du corpus
SGG, coquilles comprises. C'est important, parce que les pièges de ce module
ne sont pas théoriques — ils ont été trouvés en lisant les vraies données.

Deux d'entre eux méritent d'être nommés :

* la fiche du SGG pour la loi 2024-09 porte « du 20 févr. 204 », un millésime
  amputé. Un module qui ferait confiance au titre du portail daterait faux ;
* le champ ``publieLe`` du SGG, que LCF recopie dans ``issuedAt``, est la date
  de **mise en ligne**, pas celle de la signature. Sur cette même loi, le PDF
  porte le 20 février 2024 et la fiche le 12 mars. Les confondre daterait faux
  tout un corpus, silencieusement.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from bldp.core.crawl import (
    AGREEMENT_CONFIDENCE,
    CrawlIndexError,
    CrawlRecord,
    LcfIndex,
    reconcile,
)
from bldp.models import DocumentMetadata, DocumentType


# ---------------------------------------------------------------------------
# Un index LCF factice, au schéma réel
# ---------------------------------------------------------------------------


SCHEMA = """
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY, source_id TEXT, native_id TEXT,
    canonical_url TEXT, current_version INTEGER, status TEXT
);
CREATE TABLE document_versions (
    document_id TEXT, version_no INTEGER, content_hash TEXT, fetched_at TEXT
);
CREATE TABLE content_objects (
    content_hash TEXT PRIMARY KEY, byte_size INTEGER, mime_type TEXT,
    storage_path TEXT, verify_status TEXT
);
CREATE TABLE document_metadata (
    document_id TEXT, version_no INTEGER, raw_json TEXT,
    common_json TEXT, provenance_json TEXT
);
CREATE VIEW v_current_documents AS
SELECT d.document_id, d.source_id, d.native_id, d.canonical_url, d.status,
       v.version_no, v.fetched_at, v.content_hash,
       c.byte_size, c.mime_type, c.verify_status
FROM documents d
JOIN document_versions v ON v.document_id = d.document_id
                        AND v.version_no = d.current_version
JOIN content_objects c   ON c.content_hash = v.content_hash;
"""


#: Fiche réelle du SGG, coquille du millésime comprise.
FICHE_LOI_2024_09 = {
    "raw": {
        "categorie": "loi",
        "description": "portant loi-cadre sur la planification du développement "
                       "et sur l'évaluation des politiques publiques.",
        "numero": "2024-09",
        "publieLe": "2024-03-12",
        "tailleAnnoncee": 7340032,
        "titre": "Loi N° 2024-09 du 20 févr. 204",
    },
    "common": {
        "authority": "Secrétariat Général du Gouvernement",
        "documentKind": "loi",
        "issuedAt": "2024-03-12",
        "language": "fr",
        "reference": "2024-09",
    },
    "provenance": [
        {"field": "titre", "at": "https://sgg.gouv.bj/doc/loi-2024-09/",
         "locator": "aside.doc a.doc-title"},
        {"field": "numero", "at": "https://sgg.gouv.bj/doc/loi-2024-09/",
         "locator": "aside.doc i.num"},
    ],
}


@pytest.fixture
def data_dir(tmp_path):
    """Un magasin LCF complet : index, objets, et un PDF sur le disque."""
    racine = tmp_path / "lcf-data"
    (racine / "index").mkdir(parents=True)
    (racine / "objects" / "55" / "da").mkdir(parents=True)

    chemin_objet = "objects/55/da/55da2a9e.bin"
    (racine / chemin_objet).write_bytes(b"%PDF-1.4 faux contenu")

    db = sqlite3.connect(racine / "index" / "lcf.db")
    db.executescript(SCHEMA)
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?)",
        ("hash-interne-1", "bj.sgg.lois", "loi-2024-09",
         "https://sgg.gouv.bj/doc/loi-2024-09/", 1, "stored"),
    )
    db.execute(
        "INSERT INTO document_versions VALUES (?,?,?,?)",
        ("hash-interne-1", 1, "sha256:abc", "2026-08-31T01:33:03.935Z"),
    )
    db.execute(
        "INSERT INTO content_objects VALUES (?,?,?,?,?)",
        ("sha256:abc", 7340032, "application/pdf", chemin_objet, "ok"),
    )
    db.execute(
        "INSERT INTO document_metadata VALUES (?,?,?,?,?)",
        ("hash-interne-1", 1,
         json.dumps(FICHE_LOI_2024_09["raw"], ensure_ascii=False),
         json.dumps(FICHE_LOI_2024_09["common"], ensure_ascii=False),
         json.dumps(FICHE_LOI_2024_09["provenance"], ensure_ascii=False)),
    )
    db.commit()
    db.close()
    return racine


def fiche(**surcharges) -> CrawlRecord:
    """Une fiche de catalogue, dont on ne surcharge que ce qui compte."""
    base = {
        "document_id": "loi-2024-09",
        "source_id": "bj.sgg.lois",
        "url": "https://sgg.gouv.bj/doc/loi-2024-09/",
        "content_path": "/var/lib/lcf/data/objects/55/da/55da2a9e.bin",
        "content_hash": "sha256:abc",
        "byte_size": 7340032,
        "fetched_at": "2026-08-31T01:33:03.935Z",
        "title": "Loi N° 2024-09 du 20 févr. 204",
        "number": "2024-09",
        "category": "loi",
        "description": "portant loi-cadre sur la planification.",
        "published_at": "2024-03-12",
        "publisher": "Secrétariat Général du Gouvernement",
        "provenance": FICHE_LOI_2024_09["provenance"],
    }
    base.update(surcharges)
    from pathlib import Path

    base["content_path"] = Path(base["content_path"])
    return CrawlRecord(**base)


def metadonnees(**surcharges) -> DocumentMetadata:
    base = {"document_id": "loi_2024_09"}
    base.update(surcharges)
    return DocumentMetadata(**base)


# ---------------------------------------------------------------------------
# Lecture de l'index
# ---------------------------------------------------------------------------


class TestLectureDeLIndex:
    def test_un_index_absent_le_dit_clairement(self, tmp_path):
        with pytest.raises(CrawlIndexError) as erreur:
            LcfIndex(tmp_path / "nulle-part")
        assert "introuvable" in str(erreur.value)

    def test_les_fiches_se_lisent(self, data_dir):
        with LcfIndex(data_dir) as index:
            assert index.count() == 1
            assert index.sources() == [("bj.sgg.lois", 1)]
            f = index.get("loi-2024-09")
        assert f is not None
        assert f.number == "2024-09"
        assert f.category == "loi"
        assert f.url == "https://sgg.gouv.bj/doc/loi-2024-09/"
        assert f.content_path.exists(), "le PDF doit être résolu dans le magasin"

    def test_la_date_du_catalogue_est_rangee_sous_son_vrai_nom(self, data_dir):
        """``publieLe`` est une date de mise en ligne, pas celle de l'acte."""
        with LcfIndex(data_dir) as index:
            f = index.get("loi-2024-09")
        assert f.published_at == "2024-03-12"
        # Il ne doit exister aucun attribut qui laisserait croire le contraire.
        assert not hasattr(f, "date")

    def test_l_editeur_n_est_pas_l_autorite(self, data_dir):
        """Le SGG publie l'acte ; il ne le prend pas."""
        with LcfIndex(data_dir) as index:
            f = index.get("loi-2024-09")
        assert f.publisher == "Secrétariat Général du Gouvernement"
        assert not hasattr(f, "authority")

    def test_un_objet_annonce_mais_absent_est_ignore(self, data_dir):
        """L'index peut mentir sur le magasin : on ne fabrique pas de fiche."""
        (data_dir / "objects" / "55" / "da" / "55da2a9e.bin").unlink()
        with LcfIndex(data_dir) as index:
            assert list(index.records()) == []

    def test_la_provenance_est_conservee(self, data_dir):
        """La traçabilité champ par champ est ce que le §33 réclame."""
        with LcfIndex(data_dir) as index:
            f = index.get("loi-2024-09")
        preuve = f.evidence_for("numero")
        assert "sgg.gouv.bj" in preuve
        assert "aside.doc i.num" in preuve

    def test_l_ouverture_est_en_lecture_seule(self, data_dir):
        """Le collecteur écrit pendant qu'on lit : on ne doit rien pouvoir casser."""
        with LcfIndex(data_dir) as index:
            with pytest.raises(sqlite3.OperationalError):
                index.connection.execute("DELETE FROM documents")

    def test_l_index_doit_etre_ouvert(self, data_dir):
        index = LcfIndex(data_dir)
        with pytest.raises(CrawlIndexError) as erreur:
            index.count()
        assert "with LcfIndex" in str(erreur.value)

    def test_le_type_se_deduit_de_la_categorie(self):
        assert fiche(category="loi").document_type is DocumentType.LOI
        assert fiche(category="decret").document_type is DocumentType.DECRET
        assert fiche(category="accord").document_type is DocumentType.CONVENTION
        assert fiche(category="ordonnance").document_type is DocumentType.ORDONNANCE
        assert fiche(category="inventee").document_type is None


# ---------------------------------------------------------------------------
# Confrontation
# ---------------------------------------------------------------------------


class TestConfrontation:
    """Le document fait foi, le catalogue est un témoin."""

    def test_la_tracabilite_est_toujours_inscrite(self):
        m = metadonnees()
        reconcile(m, fiche())
        assert m.source_url == "https://sgg.gouv.bj/doc/loi-2024-09/"
        assert m.retrieved_at == "2026-08-31T01:33:03.935Z"
        assert m.source == "Secrétariat Général du Gouvernement"

    def test_un_trou_se_comble(self):
        m = metadonnees(number=None)
        ecarts = reconcile(m, fiche())
        assert m.number == "2024-09"
        assert m.confidence["number"] > 0.8
        assert "sgg.gouv.bj" in m.evidence["number"]
        assert any(e.action == "comble" and e.field == "number" for e in ecarts)

    def test_un_accord_renforce_la_confiance(self):
        m = metadonnees(number="2024-09", confidence={"number": 0.6})
        ecarts = reconcile(m, fiche())
        assert m.confidence["number"] == AGREEMENT_CONFIDENCE
        assert any(e.action == "confirme" and e.field == "number" for e in ecarts)

    def test_la_mise_en_forme_n_est_pas_une_divergence(self):
        """« N° 2024-09 » et « 2024-09 » sont le même numéro."""
        m = metadonnees(number="N° 2024-09")
        ecarts = reconcile(m, fiche())
        assert any(e.action == "confirme" for e in ecarts)

    def test_une_divergence_ne_remplace_jamais_le_document(self):
        """Le corpus reste le miroir du papier, même quand le portail insiste."""
        m = metadonnees(number="2024-90")
        ecarts = reconcile(m, fiche())
        assert m.number == "2024-90", "la lecture du document doit survivre"
        divergence = next(e for e in ecarts if e.action == "diverge")
        assert divergence.from_document == "2024-90"
        assert divergence.from_catalogue == "2024-09"
        assert any("catalogue" in w for w in m.warnings)

    def test_le_type_inconnu_se_signale_au_lieu_de_devenir_autre(self):
        m = metadonnees()
        ecarts = reconcile(m, fiche(category="machin"))
        assert m.type is DocumentType.INCONNU
        signalement = next(e for e in ecarts if e.field == "type")
        assert "CATEGORY_TO_TYPE" in signalement.message

    def test_le_type_se_comble_puis_se_confirme(self):
        m = metadonnees()
        reconcile(m, fiche())
        assert m.type is DocumentType.LOI
        m2 = metadonnees(type=DocumentType.LOI)
        ecarts = reconcile(m2, fiche())
        assert any(e.field == "type" and e.action == "confirme" for e in ecarts)

    def test_un_type_divergent_conserve_la_lecture_du_document(self):
        m = metadonnees(type=DocumentType.DECRET)
        ecarts = reconcile(m, fiche(category="loi"))
        assert m.type is DocumentType.DECRET
        assert any(e.field == "type" and e.action == "diverge" for e in ecarts)


class TestLesDatesNeSeConfondentPas:
    """Le piège principal de ce module, et le plus coûteux."""

    def test_la_date_du_catalogue_ne_comble_jamais_la_date_de_l_acte(self):
        """``publieLe`` est une date de mise en ligne. La reprendre daterait faux."""
        m = metadonnees(date=None)
        reconcile(m, fiche(published_at="2024-03-12"))
        assert m.date is None, (
            "la date de mise en ligne ne doit jamais devenir la date de l'acte"
        )

    def test_une_date_anterieure_a_la_publication_est_normale(self):
        """Le PDF dit 20 février, la fiche 12 mars : c'est l'ordre attendu."""
        m = metadonnees(date="2024-02-20")
        ecarts = reconcile(m, fiche(published_at="2024-03-12"))
        assert not [e for e in ecarts if e.field == "date"]

    def test_un_acte_signe_apres_sa_publication_est_signale(self):
        m = metadonnees(date="2024-06-01")
        ecarts = reconcile(m, fiche(published_at="2024-03-12"))
        anomalie = next(e for e in ecarts if e.field == "date")
        assert "incohérente" in anomalie.message

    def test_le_titre_du_catalogue_ne_corrige_jamais_celui_du_document(self):
        """La fiche réelle porte « du 20 févr. 204 » : un millésime amputé."""
        lu = "LOI n° 2024-09 DU 20 FEVRIER 2024 portant loi-cadre"
        m = metadonnees(title=lu)
        reconcile(m, fiche())
        assert m.title == lu, "une coquille du portail ne doit pas gagner"

    def test_le_titre_comble_seulement_un_vide(self):
        m = metadonnees(title=None)
        ecarts = reconcile(m, fiche())
        assert m.title == "Loi N° 2024-09 du 20 févr. 204"
        assert any(e.field == "title" and e.action == "comble" for e in ecarts)


class TestDeBoutEnBout:
    def test_un_document_complet_ne_produit_que_des_confirmations(self, data_dir):
        with LcfIndex(data_dir) as index:
            f = index.get("loi-2024-09")
        m = metadonnees(
            title="LOI n° 2024-09 DU 20 FEVRIER 2024",
            number="2024-09", date="2024-02-20", type=DocumentType.LOI,
        )
        ecarts = reconcile(m, f)
        assert {e.action for e in ecarts} == {"confirme"}
        assert not m.warnings
