"""Lecture de l'index d'un collecteur LCF, et confrontation au document.

Un collecteur qui aspire un portail juridique en sait long avant même qu'on
ouvre le PDF : le numéro annoncé, le titre officiel, la catégorie, l'URL de la
page d'origine. Ce module lit ce catalogue et le met en regard de ce que la
chaîne d'extraction a lu dans le document lui-même.

**Le document fait foi, le catalogue est un témoin.** C'est le même principe
que partout ailleurs ici : le corpus doit rester le miroir du papier. Un
portail est tenu par des humains qui saisissent des fiches, et ils se trompent
— on a relevé « Loi N° 2024-09 du 20 févr. 204 » sur une fiche du SGG. Quand
le document et le catalogue divergent, on garde la lecture du document et on
**signale** ; on ne remplace pas.

Le catalogue sert donc à trois choses, et pas à une quatrième :

``combler``
    Là où l'extraction n'a rien trouvé, la valeur du catalogue vaut mieux que
    rien — avec sa provenance inscrite, pour qu'on sache d'où elle sort.

``confirmer``
    Là où les deux s'accordent, la confiance monte. C'est gratuit, et c'est ce
    qui permet de ne pas relire ce qui n'en a pas besoin.

``signaler``
    Là où ils divergent, quelqu'un doit regarder. Le signalement porte les
    deux versions.

Ce que le catalogue ne fait **jamais** : donner la date de l'acte. Le champ
``publieLe`` du SGG — que LCF recopie dans ``issuedAt`` — est la date de mise
en ligne de la fiche, pas celle de la signature. Sur la loi 2024-09, le PDF
porte « 20 février 2024 » et la fiche « 2024-03-12 ». Confondre les deux
daterait faux tout un corpus juridique, silencieusement.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from bldp.logging_setup import get_logger
from bldp.models import DocumentMetadata, DocumentType
from bldp.utils import NUMERO_PREFIX

logger = get_logger("crawl.lcf")


class CrawlIndexError(RuntimeError):
    """L'index du collecteur est absent ou inexploitable."""


#: Correspondance entre la catégorie annoncée par le collecteur et le type
#: reconnu par le pipeline. Une catégorie inconnue ne devient jamais
#: ``AUTRE`` en silence : elle est signalée.
CATEGORY_TO_TYPE: dict[str, DocumentType] = {
    "loi": DocumentType.LOI,
    "lois": DocumentType.LOI,
    "code": DocumentType.CODE,
    "decret": DocumentType.DECRET,
    "décret": DocumentType.DECRET,
    "decrets": DocumentType.DECRET,
    "arrete": DocumentType.ARRETE,
    "arrêté": DocumentType.ARRETE,
    "arretes": DocumentType.ARRETE,
    # « ordon » n'est pas une faute de frappe : c'est la valeur que le
    # catalogue du SGG porte réellement pour ses 1 007 ordonnances.
    "ordon": DocumentType.ORDONNANCE,
    "ordonnance": DocumentType.ORDONNANCE,
    "ordonnances": DocumentType.ORDONNANCE,
    "decision": DocumentType.DECISION,
    "décision": DocumentType.DECISION,
    "decisions": DocumentType.DECISION,
    "accord": DocumentType.CONVENTION,
    "accords": DocumentType.CONVENTION,
    "convention": DocumentType.CONVENTION,
    "circulaire": DocumentType.CIRCULAIRE,
    "constitution": DocumentType.CONSTITUTION,
}

#: Confiance accordée à une valeur qui ne vient que du catalogue.
#:
#: Élevée sans être totale : une fiche de portail est saisie à la main, elle
#: est fiable mais elle n'est pas le texte officiel.
CATALOGUE_CONFIDENCE = 0.85

#: Confiance quand le document et le catalogue disent la même chose. Deux
#: sources indépendantes qui concordent ne laissent guère de place au doute.
AGREEMENT_CONFIDENCE = 0.98


def normalize_hash(value: str) -> str:
    """Empreinte réduite à sa forme comparable.

    Le collecteur écrit ``sha256:ab12…``, le pipeline ``ab12…``. Les deux
    désignent le même contenu ; seule la convention d'écriture diffère.
    """
    if not value:
        return ""
    return value.split(":", 1)[-1].strip().lower()


def load_catalogue(config) -> Optional[dict[str, "CrawlRecord"]]:
    """Charge le catalogue déclaré en configuration, ou ``None``.

    Une absence de catalogue n'est jamais une erreur : le pipeline sait
    travailler sans. En revanche un catalogue déclaré mais illisible en est
    une — on le dit, plutôt que de traiter tout un corpus en silence sans la
    vérification qu'on croyait avoir.
    """
    if not config.get("crawl.enabled", False):
        return None

    chemin = str(config.get("crawl.index", "") or "").strip()
    if not chemin:
        raise CrawlIndexError(
            "crawl.enabled est vrai mais crawl.index est vide : indiquez le "
            "dossier de données du collecteur, par exemple /var/lib/lcf/data."
        )

    with LcfIndex(chemin) as index:
        catalogue = index.load_by_hash()
    logger.info(
        "Catalogue de collecte chargé : %d fiche(s) depuis %s.",
        len(catalogue), chemin,
    )
    return catalogue


# ---------------------------------------------------------------------------
# Ce que le collecteur sait
# ---------------------------------------------------------------------------


@dataclass
class CrawlRecord:
    """La fiche d'un document telle que le collecteur l'a enregistrée."""

    document_id: str                 # identifiant lisible, ex. « loi-2026-14 »
    source_id: str                   # ex. « bj.sgg.lois »
    url: str                         # page d'origine — traçabilité (§33)
    content_path: Path               # le PDF, dans le magasin d'objets
    content_hash: str
    byte_size: int
    fetched_at: str

    title: Optional[str] = None
    number: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    language: str = "fr"
    #: Date de **mise en ligne de la fiche**. Jamais la date de l'acte.
    published_at: Optional[str] = None
    #: Éditeur du portail (« Secrétariat Général du Gouvernement »), à ne pas
    #: confondre avec l'autorité qui a pris l'acte.
    publisher: Optional[str] = None
    #: Pour chaque champ, d'où il a été extrait de la page.
    provenance: list[dict] = field(default_factory=list)

    @property
    def document_type(self) -> Optional[DocumentType]:
        """Type déduit de la fiche, ou ``None`` si rien ne permet de conclure.

        La catégorie est essayée d'abord, puis l'identifiant de source. Ce
        repli n'est pas un luxe : le catalogue du SGG écrit « ordon » pour ses
        1 007 ordonnances, et rien ne garantit que la prochaine source ne
        tronquera pas autrement. Le nom de la source, lui, est stable — c'est
        la collection elle-même qui le porte.
        """
        if self.category:
            connu = CATEGORY_TO_TYPE.get(self.category.strip().lower())
            if connu is not None:
                return connu
        # « bj.sgg.ordonnances » -> « ordonnances »
        if self.source_id and "." in self.source_id:
            return CATEGORY_TO_TYPE.get(self.source_id.rsplit(".", 1)[-1].lower())
        return None

    def evidence_for(self, champ: str) -> str:
        """D'où vient ce champ, en une ligne consignable."""
        for trace in self.provenance:
            if trace.get("field") == champ:
                selecteur = trace.get("locator", "")
                return f"catalogue {self.source_id} : {self.url} ({selecteur})"
        return f"catalogue {self.source_id} : {self.url}"

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "source_id": self.source_id,
            "url": self.url,
            "content_path": str(self.content_path),
            "content_hash": self.content_hash,
            "byte_size": self.byte_size,
            "fetched_at": self.fetched_at,
            "title": self.title,
            "number": self.number,
            "category": self.category,
            "description": self.description,
            "published_at": self.published_at,
            "publisher": self.publisher,
        }


# ---------------------------------------------------------------------------
# L'index
# ---------------------------------------------------------------------------


#: Une seule requête, jointe une fois pour toutes. Les vues du collecteur
#: (``v_current_documents``) donnent déjà la version courante de chaque
#: document : on ne redéroule pas l'historique, on lit l'état présent.
_REQUETE = """
SELECT
    v.document_id      AS interne,
    d.native_id        AS identifiant,
    v.source_id        AS source,
    v.canonical_url    AS url,
    v.content_hash     AS empreinte,
    v.byte_size        AS taille,
    v.fetched_at       AS collecte,
    c.storage_path     AS chemin,
    m.common_json      AS commun,
    m.raw_json         AS brut,
    m.provenance_json  AS provenance
FROM v_current_documents v
JOIN documents d          ON d.document_id = v.document_id
JOIN content_objects c    ON c.content_hash = v.content_hash
LEFT JOIN document_metadata m
       ON m.document_id = v.document_id AND m.version_no = v.version_no
WHERE v.verify_status = 'ok'
"""


class LcfIndex:
    """Accès en **lecture seule** à l'index d'un collecteur LCF.

    Le collecteur écrit dans cette base pendant qu'on la lit : l'ouverture est
    donc explicitement en lecture seule, et jamais en écriture. Rien de ce que
    fait le pipeline ne doit pouvoir abîmer la collecte en cours.

    S'utilise comme gestionnaire de contexte ::

        with LcfIndex("/var/lib/lcf/data") as index:
            for fiche in index.records(source_id="bj.sgg.lois"):
                ...
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "index" / "lcf.db"
        self.objects_dir = self.data_dir
        if not self.db_path.exists():
            raise CrawlIndexError(
                f"Index du collecteur introuvable : {self.db_path}. "
                "Vérifiez le chemin de données passé à LcfIndex."
            )
        self._connection: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "LcfIndex":
        # « mode=ro » et non « immutable » : le fichier change sous nos pieds,
        # et on veut lire l'état courant, pas un instantané figé.
        self._connection = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True, timeout=30.0
        )
        self._connection.row_factory = sqlite3.Row
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CrawlIndexError(
                "Index non ouvert : utilisez « with LcfIndex(...) as index »."
            )
        return self._connection

    # -- lecture -----------------------------------------------------------

    def sources(self) -> list[tuple[str, int]]:
        """Les sources collectées et leur nombre de documents exploitables."""
        rows = self.connection.execute(
            "SELECT source_id, COUNT(*) AS n FROM v_current_documents "
            "WHERE verify_status = 'ok' GROUP BY source_id ORDER BY n DESC"
        ).fetchall()
        return [(row["source_id"], row["n"]) for row in rows]

    def count(self, source_id: Optional[str] = None) -> int:
        requete = (
            "SELECT COUNT(*) FROM v_current_documents WHERE verify_status = 'ok'"
        )
        parametres: tuple = ()
        if source_id:
            requete += " AND source_id = ?"
            parametres = (source_id,)
        return int(self.connection.execute(requete, parametres).fetchone()[0])

    def records(
        self,
        source_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Iterator[CrawlRecord]:
        """Parcourt les fiches, sans jamais charger le corpus en mémoire."""
        requete = _REQUETE
        parametres: list = []
        if source_id:
            requete += " AND v.source_id = ?"
            parametres.append(source_id)
        requete += " ORDER BY d.native_id"
        if limit is not None:
            requete += " LIMIT ? OFFSET ?"
            parametres.extend([limit, offset])

        for row in self.connection.execute(requete, parametres):
            fiche = self._to_record(row)
            if fiche is not None:
                yield fiche

    def get(self, native_id: str) -> Optional[CrawlRecord]:
        """La fiche d'un document, par son identifiant lisible."""
        row = self.connection.execute(
            _REQUETE + " AND d.native_id = ?", (native_id,)
        ).fetchone()
        return self._to_record(row) if row else None

    def load_by_hash(self) -> dict[str, CrawlRecord]:
        """Tout le catalogue en mémoire, indexé par empreinte de contenu.

        Deux raisons de tout charger d'un coup plutôt que d'interroger par
        document. D'abord le **volume** : quelques milliers de fiches tiennent
        sans peine en mémoire, là où autant d'ouvertures de base coûteraient
        plus que le traitement lui-même. Ensuite les **fils** : le pipeline
        traite en parallèle, et une connexion SQLite ne se partage pas entre
        fils — un dictionnaire, si.

        L'empreinte est la bonne clef de jointure : elle désigne le contenu,
        pas le nom du fichier. Un document renommé, déplacé ou recopié
        retrouve sa fiche ; deux fichiers identiques la partagent.
        """
        catalogue: dict[str, CrawlRecord] = {}
        for fiche in self.records():
            empreinte = normalize_hash(fiche.content_hash)
            if empreinte:
                catalogue[empreinte] = fiche
        return catalogue

    def _to_record(self, row: sqlite3.Row) -> Optional[CrawlRecord]:
        chemin = self.objects_dir / row["chemin"]
        if not chemin.exists():
            # L'index annonce un objet que le magasin n'a pas. On ne fabrique
            # pas de fiche pour un fichier absent : le document est ignoré et
            # l'anomalie tracée.
            logger.warning(
                "%s : objet annoncé mais introuvable (%s)",
                row["identifiant"], chemin,
            )
            return None

        commun = _charger_json(row["commun"])
        brut = _charger_json(row["brut"])
        provenance = _charger_json(row["provenance"], defaut=[])

        return CrawlRecord(
            document_id=row["identifiant"],
            source_id=row["source"],
            url=row["url"] or "",
            content_path=chemin,
            content_hash=row["empreinte"] or "",
            byte_size=int(row["taille"] or 0),
            fetched_at=row["collecte"] or "",
            title=brut.get("titre") or None,
            number=brut.get("numero") or commun.get("reference") or None,
            category=commun.get("documentKind") or brut.get("categorie") or None,
            description=brut.get("description") or None,
            language=commun.get("language") or "fr",
            # « publieLe » et « issuedAt » portent la même valeur : la date de
            # mise en ligne. Elle est stockée telle quelle, sous son vrai nom.
            published_at=brut.get("publieLe") or commun.get("issuedAt") or None,
            publisher=commun.get("authority") or None,
            provenance=provenance if isinstance(provenance, list) else [],
        )


def _charger_json(valeur, defaut=None):
    if not valeur:
        return {} if defaut is None else defaut
    try:
        return json.loads(valeur)
    except (json.JSONDecodeError, TypeError):
        return {} if defaut is None else defaut


# ---------------------------------------------------------------------------
# Confrontation
# ---------------------------------------------------------------------------


@dataclass
class Discrepancy:
    """Un écart entre ce que dit le document et ce qu'annonce le catalogue."""

    field: str
    from_document: Optional[str]
    from_catalogue: Optional[str]
    action: str          # « comble » | « confirme » | « diverge »
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "from_document": self.from_document,
            "from_catalogue": self.from_catalogue,
            "action": self.action,
            "message": self.message,
            "severity": self.severity,
        }


def reconcile(
    metadata: DocumentMetadata, record: CrawlRecord
) -> list[Discrepancy]:
    """Confronte les métadonnées lues au document à la fiche du catalogue.

    ``metadata`` est modifié en place : la traçabilité est toujours inscrite,
    les trous sont comblés, les accords renforcent la confiance. Une valeur
    lue dans le document n'est **jamais** remplacée par celle du catalogue —
    la divergence est renvoyée pour qu'un humain tranche.

    Returns:
        Les écarts constatés, comblements et confirmations compris. Rien n'est
        silencieux : chaque décision est représentée.
    """
    ecarts: list[Discrepancy] = []

    # -- traçabilité : toujours, et sans discussion ------------------------
    metadata.source_url = record.url or metadata.source_url
    metadata.retrieved_at = record.fetched_at or metadata.retrieved_at
    if record.publisher and not metadata.source:
        # L'éditeur du portail renseigne « source », pas « authority » : le
        # SGG publie l'acte, il ne le prend pas.
        metadata.source = record.publisher

    ecarts.extend(_reconcilier_valeur(
        metadata, "number", metadata.number, record.number, record,
    ))
    ecarts.extend(_reconcilier_type(metadata, record))
    ecarts.extend(_reconcilier_titre(metadata, record))
    ecarts.extend(_verifier_coherence_des_dates(metadata, record))

    return ecarts


def _reconcilier_valeur(
    metadata: DocumentMetadata,
    champ: str,
    valeur_document: Optional[str],
    valeur_catalogue: Optional[str],
    record: CrawlRecord,
) -> list[Discrepancy]:
    """Règle commune : combler, confirmer, ou signaler."""
    if not valeur_catalogue:
        return []

    if not valeur_document:
        setattr(metadata, champ, valeur_catalogue)
        metadata.confidence[champ] = CATALOGUE_CONFIDENCE
        metadata.evidence[champ] = record.evidence_for(champ)
        return [Discrepancy(
            field=champ, from_document=None, from_catalogue=valeur_catalogue,
            action="comble", severity="info",
            message=(
                f"{champ} absent du document, repris du catalogue : "
                f"{valeur_catalogue!r}"
            ),
        )]

    if _equivalents(valeur_document, valeur_catalogue):
        metadata.confidence[champ] = max(
            metadata.confidence.get(champ, 0.0), AGREEMENT_CONFIDENCE
        )
        ancienne = metadata.evidence.get(champ, "")
        metadata.evidence[champ] = (
            f"{ancienne} | confirmé par {record.evidence_for(champ)}"
            if ancienne else f"confirmé par {record.evidence_for(champ)}"
        )
        return [Discrepancy(
            field=champ, from_document=valeur_document,
            from_catalogue=valeur_catalogue, action="confirme", severity="info",
            message=f"{champ} confirmé par le catalogue : {valeur_document!r}",
        )]

    # Divergence : on garde la lecture du document, on signale.
    metadata.warnings.append(
        f"{champ} : le document lit {valeur_document!r}, "
        f"le catalogue annonce {valeur_catalogue!r}"
    )
    return [Discrepancy(
        field=champ, from_document=valeur_document,
        from_catalogue=valeur_catalogue, action="diverge", severity="warning",
        message=(
            f"{champ} : le document lit {valeur_document!r}, le catalogue "
            f"annonce {valeur_catalogue!r}. La lecture du document est "
            "conservée ; à vérifier."
        ),
    )]


def _reconcilier_type(
    metadata: DocumentMetadata, record: CrawlRecord
) -> list[Discrepancy]:
    """Le type annoncé par le catalogue est fiable : c'est un choix de menu."""
    if not record.category:
        return []

    attendu = record.document_type
    if attendu is None:
        return [Discrepancy(
            field="type", from_document=metadata.type.value,
            from_catalogue=record.category, action="diverge", severity="warning",
            message=(
                f"catégorie inconnue du pipeline : {record.category!r}. "
                "Ajoutez-la à CATEGORY_TO_TYPE plutôt que de la classer "
                "« autre » en silence."
            ),
        )]

    if metadata.type in (DocumentType.INCONNU, DocumentType.AUTRE):
        metadata.type = attendu
        metadata.confidence["type"] = CATALOGUE_CONFIDENCE
        metadata.evidence["type"] = record.evidence_for("categorie")
        return [Discrepancy(
            field="type", from_document=None, from_catalogue=attendu.value,
            action="comble", severity="info",
            message=f"type repris du catalogue : {attendu.value}",
        )]

    if metadata.type is attendu:
        metadata.confidence["type"] = max(
            metadata.confidence.get("type", 0.0), AGREEMENT_CONFIDENCE
        )
        return [Discrepancy(
            field="type", from_document=metadata.type.value,
            from_catalogue=attendu.value, action="confirme", severity="info",
            message=f"type confirmé : {attendu.value}",
        )]

    metadata.warnings.append(
        f"type : le document semble un {metadata.type.value}, "
        f"le catalogue annonce un {attendu.value}"
    )
    return [Discrepancy(
        field="type", from_document=metadata.type.value,
        from_catalogue=attendu.value, action="diverge", severity="warning",
        message=(
            f"type : document lu comme {metadata.type.value}, catalogue "
            f"annonce {attendu.value}. Lecture du document conservée."
        ),
    )]


def _reconcilier_titre(
    metadata: DocumentMetadata, record: CrawlRecord
) -> list[Discrepancy]:
    """Le titre du catalogue ne comble qu'un vide.

    Il n'est jamais opposé à celui du document : un portail abrège, réécrit,
    et se trompe — « Loi N° 2024-09 du 20 févr. 204 » est une fiche réelle du
    SGG. Comparer deux formulations libres produirait un bruit constant sans
    rien apprendre.
    """
    if not record.title or metadata.title:
        return []
    metadata.title = record.title
    metadata.confidence["title"] = CATALOGUE_CONFIDENCE
    metadata.evidence["title"] = record.evidence_for("titre")
    return [Discrepancy(
        field="title", from_document=None, from_catalogue=record.title,
        action="comble", severity="info",
        message=f"titre absent du document, repris du catalogue : {record.title!r}",
    )]


def _verifier_coherence_des_dates(
    metadata: DocumentMetadata, record: CrawlRecord
) -> list[Discrepancy]:
    """Contrôle de bon sens : un acte ne se signe pas après sa publication.

    La date du catalogue n'est **pas** celle de l'acte, elle ne peut donc pas
    combler ``date`` ni la confirmer. Elle sert seulement de borne : si la
    lecture du document est postérieure à la mise en ligne de la fiche, l'une
    des deux est fausse, et cela vaut d'être vu.
    """
    if not (metadata.date and record.published_at):
        return []
    if metadata.date <= record.published_at:
        return []
    return [Discrepancy(
        field="date", from_document=metadata.date,
        from_catalogue=record.published_at, action="diverge", severity="warning",
        message=(
            f"date incohérente : le document est daté du {metadata.date}, "
            f"postérieur à sa mise en ligne le {record.published_at}. "
            "L'une des deux lectures est fausse."
        ),
    )]


#: Préfixe « n° », « no », « N º »… à retirer avant de comparer deux numéros.
_PREFIXE_NUMERO = re.compile(r"^\s*" + NUMERO_PREFIX, re.IGNORECASE)


def _equivalents(a: str, b: str) -> bool:
    """Deux valeurs disent-elles la même chose, à la mise en forme près ?

    « 2024-09 », « 2024‑09 » et « N° 2024-09 » sont le même numéro : le tiret
    peut être un tiret cadratin, l'espace peut manquer, et le portail écrit
    rarement le préfixe comme le document. On retire donc ce préfixe, puis on
    ne compare que les caractères alphanumériques.

    Ce que l'on ne fait **pas** : rapprocher deux numéros qui se ressemblent.
    « 2024-09 » et « 2024-90 » restent différents, et doivent le rester — une
    inversion de chiffres est exactement le genre d'erreur qu'on cherche.
    """
    reduire = lambda v: "".join(
        c for c in _PREFIXE_NUMERO.sub("", str(v)).lower() if c.isalnum()
    )
    return reduire(a) == reduire(b)
