"""Le document et ses témoins : la fiche du portail, l'adresse SGG.

Tous les cas viennent du corpus réel, vérification du lot 1 (3 octobre 2026) :

* l'arrêté 2018-002, daté du visa de l'élection de 2016 (« 2016-03-20 ») alors
  que sa fiche annonce « du 25 avril 2018 », et lu « 2018-002/PR/ » quand la
  fiche dit « 2018-002 » — une fausse divergence ;
* le décret 2016-711, lu « 2016-292 » parce qu'il cite le décret 2016-292 :
  indexé sous ce numéro, il captait les citations du vrai 2016-292 ;
* les coquilles « 9073-62 » (OCR) et « decret-2208-222 » (portail).

La règle ne change pas : **le document fait foi**. Un témoin confirme, ou il
est proposé à côté ; il ne remplace jamais.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bldp.core.crawl.lcf import (
    AGREEMENT_CONFIDENCE,
    CrawlRecord,
    date_dans_un_titre,
    reconcile,
)
from bldp.core.relations import (
    REFERENCE_AMBIGUE,
    annotate_relations,
    build_reference_index,
    common_root,
    detect_relations,
    document_reference_keys,
    official_reference,
    resolve_relations,
)
from bldp.core.storage.sqlite_store import LegalDatabase, load_document
from bldp.core.validation.quality import evaluate
from bldp.models import (
    Article,
    Document,
    DocumentMetadata,
    DocumentType,
    ExtractionMethod,
    ExtractionResult,
    Page,
    SourceFile,
)
from bldp.utils import official_number_key, same_official_number, utc_now_iso


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------


def document(
    document_id: str,
    text: str = "",
    doc_type: DocumentType = DocumentType.DECRET,
    number: str | None = None,
    url: str | None = None,
) -> Document:
    source = SourceFile(
        document_id=document_id,
        source_path=f"/input/{document_id}.pdf",
        filename=f"{document_id}.pdf",
        extension=".pdf",
        size_bytes=1000,
        file_hash=(document_id * 8)[:64].ljust(64, "0"),
        ingested_at=utc_now_iso(),
    )
    pages = [Page(document_id=document_id, page=1, text=text, source_file=source.filename)] if text else []
    return Document(
        document_id=document_id,
        source=source,
        metadata=DocumentMetadata(
            document_id=document_id, type=doc_type, number=number, source_url=url,
            title="Décret portant nomination", date="2020-01-15", source="SGG",
        ),
        extraction=ExtractionResult(
            document_id=document_id, source_file=source.filename,
            method=ExtractionMethod.NATIVE, pages=pages,
        ),
        articles=[
            Article(
                article_id=f"{document_id}_article_1", document_id=document_id,
                article_number="1", text=(text or "Article 1er : texte.")[:200],
                page_start=1, page_end=1,
            )
        ],
    )


def fiche(**surcharges) -> CrawlRecord:
    base = {
        "document_id": "arrete-2018-002",
        "source_id": "bj.sgg.arretes",
        "url": "https://sgg.gouv.bj/doc/arrete-2018-002/",
        "content_path": Path("/nulle-part.bin"),
        "content_hash": "sha256:abc",
        "byte_size": 433544,
        "fetched_at": "2026-08-31T05:35:02.568Z",
        "title": "Arrêté N° 2018-002 du 25 avril 2018",
        "number": "2018-002",
        "category": "arrete",
        "description": "fixant les modalités de gestion des stocks à la Présidence de la République.",
        "published_at": "2018-05-18",
        "publisher": "Secrétariat Général du Gouvernement",
    }
    base.update(surcharges)
    return CrawlRecord(**base)


def metadonnees(**surcharges) -> DocumentMetadata:
    base = {"document_id": "arrete_2018_002", "type": DocumentType.ARRETE}
    base.update(surcharges)
    return DocumentMetadata(**base)


VISA_2016_292 = (
    "Vu le décret n° 2016-292 du 17 mai 2016 fixant la structure-type des "
    "ministères ; Article 1er : Le présent décret porte nomination."
)


# ---------------------------------------------------------------------------
# Numéros officiels : une même référence, plusieurs notations
# ---------------------------------------------------------------------------


class TestNumerosOfficiels:
    @pytest.mark.parametrize(
        "brut, attendu",
        [
            ("2018-002", (2018, 2, "")),
            ("N° 2018-002/PR/", (2018, 2, "")),
            ("2018-002/PR/SGG", (2018, 2, "")),
            ("96-532", (1996, 532, "")),
            ("2016–711", (2016, 711, "")),
            ("2015-18 bis", (2015, 18, "bis")),
            ("1996_03", (1996, 3, "")),
        ],
    )
    def test_decomposition(self, brut, attendu):
        assert official_number_key(brut) == attendu

    @pytest.mark.parametrize("brut", ["", None, "2018-002 A", "12", "05-123"])
    def test_ce_qui_ne_se_decompose_pas_ne_conclut_pas(self, brut):
        """« 05-123 » : 1905 ou 2005 ? Le corpus ne permet pas de choisir."""
        assert official_number_key(brut) is None

    @pytest.mark.parametrize(
        "a, b",
        [("2018-002/PR/", "2018-002"), ("2015-18", "2015-018"), ("96-532", "1996-532"), ("N° 2024-09", "2024-09")],
    )
    def test_meme_texte_autre_notation(self, a, b):
        assert same_official_number(a, b)

    @pytest.mark.parametrize(
        "a, b",
        [
            ("2024-09", "2024-90"),     # inversion de chiffres : l'erreur qu'on cherche
            ("2015-18", "2015-18 bis"),  # une variante est un autre texte
            ("2016-292", "2016-711"),
            ("2018-002", None),
        ],
    )
    def test_textes_differents(self, a, b):
        assert not same_official_number(a, b)


# ---------------------------------------------------------------------------
# La fiche du portail
# ---------------------------------------------------------------------------


class TestLaDateDuTitreDeLaFiche:
    @pytest.mark.parametrize(
        "titre, numero, attendu",
        [
            ("Arrêté N° 2018-002 du 25 avril 2018", "2018-002", "2018-04-25"),
            ("Accord N° 2019-230 du 31 juil. 2019", "2019-230", "2019-07-31"),
            ("Décret N° 2020-12 du 1er mars 2020", "2020-12", "2020-03-01"),
            ("Loi N° 2019-40 du 7 novembre 2019", None, "2019-11-07"),
            ("Loi N° 2025-01 du 30 déc. 2024", "2025-01", "2024-12-30"),  # un an d'écart : admis
        ],
    )
    def test_lecture(self, titre, numero, attendu):
        assert date_dans_un_titre(titre, numero) == attendu

    @pytest.mark.parametrize(
        "titre, numero",
        [
            ("Loi N° 2024-09 du 20 févr. 204", "2024-09"),       # fiche réelle : millésime amputé
            ("Décret N° 2020-12 du 31 févr. 2020", "2020-12"),   # date impossible
            ("Décret N° 2020-12 du 3 brumaire 2020", "2020-12"),
            ("Arrêté N° 2018-002 du 20 mars 2016", "2018-002"),  # la fiche se contredit
            ("Arrêté N° 2018-002", "2018-002"),
            (None, None),
        ],
    )
    def test_pas_de_faux_temoin(self, titre, numero):
        assert date_dans_un_titre(titre, numero) is None

    def test_une_date_concordante_est_confirmee(self):
        m = metadonnees(date="2018-04-25", confidence={"date": 0.6})
        ecarts = reconcile(m, fiche())
        assert m.confidence["date"] == AGREEMENT_CONFIDENCE
        assert any(e.field == "date" and e.action == "confirme" for e in ecarts)

    def test_une_date_divergente_est_proposee_jamais_recopiee(self):
        """Cas réel : l'arrêté 2018-002 daté du visa de l'élection de 2016."""
        m = metadonnees(date="2016-03-20")
        ecarts = reconcile(m, fiche())
        assert m.date == "2016-03-20", "la lecture du document doit survivre"
        ecart = next(e for e in ecarts if e.field == "date" and e.action == "diverge")
        assert (ecart.from_document, ecart.from_catalogue) == ("2016-03-20", "2018-04-25")
        assert any("titre de la fiche" in w for w in m.warnings)

    def test_une_date_absente_n_est_pas_comblee(self):
        """La règle du module tient : le catalogue ne donne jamais la date."""
        m = metadonnees(date=None)
        ecarts = reconcile(m, fiche())
        assert m.date is None
        ecart = next(e for e in ecarts if e.field == "date")
        assert ecart.action == "propose" and ecart.from_catalogue == "2018-04-25"


class TestLesNotationsDuNumero:
    def test_le_code_de_service_n_est_pas_une_divergence(self):
        m = metadonnees(number="2018-002/PR/")
        ecarts = reconcile(m, fiche())
        assert m.number == "2018-002/PR/"
        assert any(e.field == "number" and e.action == "confirme" for e in ecarts)
        assert not [e for e in ecarts if e.field == "number" and e.action == "diverge"]

    def test_le_zero_de_cadrage_n_est_pas_une_divergence(self):
        m = metadonnees(number="2018-02")
        ecarts = reconcile(m, fiche())
        assert any(e.field == "number" and e.action == "confirme" for e in ecarts)

    def test_un_autre_numero_reste_une_divergence(self):
        m = metadonnees(number="2018-020")
        ecarts = reconcile(m, fiche())
        assert m.number == "2018-020"
        assert any(e.field == "number" and e.action == "diverge" for e in ecarts)


class TestLeTitreDeLaFicheEstConserve:
    def test_titre_et_description_rejoignent_les_preuves(self):
        lu = "ARRÈTÉ N\" 2018. OO2 /PR/ fixant les modalités de gestion oo2"
        m = metadonnees(title=lu)
        reconcile(m, fiche())
        assert m.title == lu, "le champ title reste le miroir du document"
        assert m.evidence["titre_catalogue"] == (
            "Arrêté N° 2018-002 du 25 avril 2018 fixant les modalités de gestion "
            "des stocks à la Présidence de la République."
        )


# ---------------------------------------------------------------------------
# L'adresse SGG, second témoin du numéro
# ---------------------------------------------------------------------------


class TestLAdresseSGG:
    @pytest.mark.parametrize(
        "url, attendu",
        [
            ("https://sgg.gouv.bj/doc/decret-2016-711/", ("decret", "2016-711")),
            ("https://sgg.gouv.bj/doc/decret-2016%E2%80%93711/", ("decret", "2016-711")),
            ("https://sgg.gouv.bj/doc/loi-90-32/", ("loi", "90-32")),
            ("https://sgg.gouv.bj/doc/ordonnance-1996-03-bis/", ("ordonnance", "1996-03-bis")),
        ],
    )
    def test_lecture(self, url, attendu):
        assert official_reference(document("x", url=url)) == attendu

    def test_une_decision_ne_devient_pas_un_decret(self):
        """``_canonical_kind`` ne regarde que « déc… » : le piège est évité ici."""
        d = document("x", url="https://sgg.gouv.bj/doc/decision-2022-338/")
        assert official_reference(d) == ("decision", "2022-338")

    def test_sans_adresse_rien(self):
        assert official_reference(document("x")) is None


class TestLesClesDUnDocument:
    def test_accord_des_deux_temoins_toutes_les_notations(self):
        d = document("decret_1996_532", number="96-532", url="https://sgg.gouv.bj/doc/decret-1996-532/")
        cles = document_reference_keys(d)
        assert {("decret", "1996-532"), ("decret", "96-532")} <= cles

    def test_le_type_du_portail_l_emporte_sur_celui_lu(self):
        """Une loi que la lecture a prise pour un « code » est citée « loi »."""
        d = document("loi_2008_09", doc_type=DocumentType.CODE, number="2008-09",
                     url="https://sgg.gouv.bj/doc/loi-2008-09/")
        cles = document_reference_keys(d)
        assert ("loi", "2008-009") in cles
        assert not any(kind == "code" for kind, _ in cles)

    def test_une_ordonnance_lue_decret_ne_dispute_pas_le_numero_du_decret(self):
        """La numérotation est propre à chaque type : cas réel ordonnance/décret 1966-53."""
        ordonnance = document("ordonnance_1966_53", doc_type=DocumentType.DECRET, number="1966-053",
                              url="https://sgg.gouv.bj/doc/ordonnance-1966-53/")
        decret = document("decret_1966_53", number="1966-053", url="https://sgg.gouv.bj/doc/decret-1966-53/")
        index = build_reference_index([ordonnance, decret])
        assert index[("decret", "1966-053")] == "decret_1966_53"
        assert index[("ordonnance", "1966-053")] == "ordonnance_1966_53"

    def test_une_adresse_accord_garde_le_type_lu(self):
        """« accord-2019-230 » héberge un décret : le type lu sert, en témoin faible."""
        d = document("accord_2019_230", number="2019-230", url="https://sgg.gouv.bj/doc/accord-2019-230/")
        assert ("decret", "2019-230") in document_reference_keys(d)

    def test_un_numero_illisible_garde_sa_graphie(self):
        """« 076-26 » : les textes qui le citent écrivent souvent de même."""
        d = document("decret_1976_26", number="076-26", url="https://sgg.gouv.bj/doc/decret-1976-26/")
        cles = document_reference_keys(d)
        assert {("decret", "1976-026"), ("decret", "076-026")} <= cles

    def test_le_code_de_service_ne_cache_pas_le_numero(self):
        d = document("arrete_2018_002", doc_type=DocumentType.ARRETE, number="2018-002/PR/",
                     url="https://sgg.gouv.bj/doc/arrete-2018-002/")
        assert ("arrete", "2018-002") in document_reference_keys(d)

    def test_desaccord_plausible_aucune_cle(self):
        """Le décret 2016-711 lu « 2016-292 » : ni l'un ni l'autre."""
        d = document("decret_2016_e2_80_93711", number="2016-292",
                     url="https://sgg.gouv.bj/doc/decret-2016%E2%80%93711/")
        assert document_reference_keys(d) == set()

    def test_un_numero_lu_impossible_cede_a_l_adresse(self):
        d = document("decret_1973_62", number="9073-62", url="https://sgg.gouv.bj/doc/decret-1973-62/")
        cles = document_reference_keys(d)
        assert ("decret", "1973-062") in cles
        assert not any(form.startswith("9073") for _, form in cles)

    def test_une_adresse_impossible_cede_a_la_lecture(self):
        d = document("decret_2008_222", number="2008-222", url="https://sgg.gouv.bj/doc/decret-2208-222/")
        cles = document_reference_keys(d)
        assert ("decret", "2008-222") in cles
        assert not any(form.startswith("2208") for _, form in cles)

    def test_sans_adresse_le_numero_lu_suffit(self):
        d = document("loi_a", doc_type=DocumentType.LOI, number="2026-1")
        assert document_reference_keys(d) == {("loi", "2026-001")}


class TestAucunPremierVenu:
    def test_deux_textes_sans_parente_rendent_la_cle_ambigue(self):
        a = document("decret_2016_292", number="2016-292")
        b = document("decret_2016_711", number="2016-292")
        assert build_reference_index([a, b])[("decret", "2016-292")] == REFERENCE_AMBIGUE

    def test_deux_editions_d_un_meme_texte_partagent_la_cle(self):
        """« bis » et cadrage du numéro : même racine, pas de conflit."""
        a = document("ordonnance_1996_03", doc_type=DocumentType.ORDONNANCE, number="1996-03")
        b = document("ordonnance_1996_03_bis", doc_type=DocumentType.ORDONNANCE, number="1996-03")
        c = document("ordonnance_1996_003", doc_type=DocumentType.ORDONNANCE, number="1996-003")
        assert build_reference_index([a, b, c])[("ordonnance", "1996-003")] == "ordonnance_1996_03"
        assert common_root("ordonnance_1996_03_bis") == common_root("ordonnance_1996_003")

    def test_une_citation_vers_une_cle_ambigue_n_est_pas_rattachee(self, config):
        a = document("decret_2016_292", number="2016-292")
        b = document("decret_2016_711", number="2016-292")
        citant = document("decret_2020_001", VISA_2016_292, number="2020-001")
        report = annotate_relations([a, b, citant], config)
        visa = next(r for r in citant.relations if "2016-292" in r.target_reference)
        assert visa.target_document_id is None and visa.needs_review is True
        assert report.ambiguous >= 1
        assert any("sans parenté" in w for w in report.warnings)

    def test_le_cas_reel_l_adresse_rend_la_citation_au_bon_texte(self, config):
        faux = document("decret_2016_e2_80_93711", number="2016-292",
                        url="https://sgg.gouv.bj/doc/decret-2016%E2%80%93711/")
        vrai = document("decret_2016_292", number="2016-292", url="https://sgg.gouv.bj/doc/decret-2016-292/")
        citant = document("decret_2020_001", VISA_2016_292, number="2020-001")
        annotate_relations([faux, vrai, citant], config)
        visa = next(r for r in citant.relations if "2016-292" in r.target_reference)
        assert visa.target_document_id == "decret_2016_292"

    def test_un_texte_du_lot_moins_atteste_ne_prend_pas_la_cle_enregistree(self, config):
        """Sans adresse, l'intrus n'a qu'un témoin faible : le texte enregistré reste."""
        externe = {("decret", "2016-292"): "decret_2016_292"}
        intrus = document("decret_2016_711", number="2016-292")
        citant = document("decret_2020_001", VISA_2016_292, number="2020-001")
        for d in (intrus, citant):
            d.relations = detect_relations(d, config)
        resolve_relations([intrus, citant], config, external_index=externe)
        visa = next(r for r in citant.relations if "2016-292" in r.target_reference)
        assert visa.target_document_id == "decret_2016_292"

    def test_une_seconde_passe_retire_une_cible_devenue_douteuse(self, config):
        """La cible fausse d'une première résolution ne survit pas à la seconde."""
        a = document("decret_2016_292", number="2016-292")
        b = document("decret_2016_711", number="2016-292")
        citant = document("decret_2020_001", VISA_2016_292, number="2020-001")
        citant.relations = detect_relations(citant, config)
        visa = next(r for r in citant.relations if "2016-292" in r.target_reference)
        visa.target_document_id = "decret_2016_711"          # héritée d'une passe antérieure
        resolve_relations([a, b, citant], config)
        assert visa.target_document_id is None

    def test_le_mieux_atteste_l_emporte_sur_un_texte_sans_parente(self):
        """Cas réel : « decret_1989_20 », sans adresse, lu comme loi 89-20."""
        faible = document("decret_1989_20", doc_type=DocumentType.LOI, number="89-20")
        fort = document("loi_1989_020", doc_type=DocumentType.LOI, number="1989-020",
                        url="https://sgg.gouv.bj/doc/loi-1989-020/")
        for ordre in ([faible, fort], [fort, faible]):
            assert build_reference_index(ordre)[("loi", "89-020")] == "loi_1989_020"

    def test_a_force_egale_la_cle_reste_ambigue(self):
        a = document("decret_2016_292", number="2016-292", url="https://sgg.gouv.bj/doc/decret-2016-292/")
        b = document("decret_2016_292_doublon_x", number="2016-292", url="https://sgg.gouv.bj/doc/decret-2016-292/")
        assert build_reference_index([a, b])[("decret", "2016-292")] == REFERENCE_AMBIGUE

    def test_l_edition_citee_telle_quelle_garde_la_cle(self):
        """« loi 90-032 » cite loi_90_032, pas son autre édition loi_1990_032."""
        courte = document("loi_90_032", doc_type=DocumentType.LOI, number="90-032",
                          url="https://sgg.gouv.bj/doc/loi-90-32/")
        longue = document("loi_1990_032", doc_type=DocumentType.LOI, number="1990-032",
                          url="https://sgg.gouv.bj/doc/loi-1990-032/")
        for ordre in ([courte, longue], [longue, courte]):
            index = build_reference_index(ordre)
            assert index[("loi", "90-032")] == "loi_90_032"
            assert index[("loi", "1990-032")] == "loi_1990_032"


# ---------------------------------------------------------------------------
# Les écarts sont conservés, et relisibles
# ---------------------------------------------------------------------------

ECART_DATE = {
    "field": "date", "action": "diverge", "severity": "warning",
    "from_document": "2016-03-20", "from_catalogue": "2018-04-25",
    "message": "date : le document lit 2016-03-20, le titre de la fiche annonce 2018-04-25",
}


class TestLesEcartsDeviennentDesAnomalies:
    def test_une_divergence_devient_un_avertissement(self, config):
        d = document("arrete_2018_002", "Article 1er : texte de l'arrêté.", number="2018-002")
        d.metadata.divergences = [ECART_DATE]
        issue = next(i for i in evaluate(d, config).issues if i.code == "diverge_du_catalogue_date")
        assert issue.severity == "warning" and "2018-04-25" in issue.message

    def test_une_proposition_est_une_information(self, config):
        d = document("arrete_2018_002", "Article 1er : texte de l'arrêté.", number="2018-002")
        d.metadata.divergences = [{**ECART_DATE, "action": "propose", "from_document": None}]
        assert any(i.code == "propose_par_le_catalogue_date" and i.severity == "info"
                   for i in evaluate(d, config).issues)

    def test_sans_ecart_rien_de_plus(self, config):
        d = document("arrete_2018_002", "Article 1er : texte de l'arrêté.", number="2018-002")
        assert not [i for i in evaluate(d, config).issues if "catalogue" in i.code]

    def test_les_ecarts_survivent_a_l_enregistrement(self, tmp_path):
        d = document("arrete_2018_002", "Article 1er : texte de l'arrêté.", number="2018-002")
        d.metadata.divergences = [ECART_DATE]
        with LegalDatabase(tmp_path / "legal_database.sqlite") as base:
            base.save_document(d)
            relu = load_document(base, d.document_id)
        assert relu.metadata.divergences == [ECART_DATE]
