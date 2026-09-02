"""Registre de suivi des documents : tickets, étapes et journal.

Le pipeline sait traiter un document ; il ne sait pas dire **où on en est**
avec lui. Ce module tient ce rôle, autour de trois idées.

**Un ticket par document, identifié par son empreinte.**
    Le ticket est attaché au ``file_hash``, pas au nom de fichier. Le même
    texte reçu deux fois — sous deux noms, dans deux dossiers, à six mois
    d'écart — retrouve son ticket d'origine. C'est ce qui empêche de refaire
    le travail déjà fait, y compris le travail *humain*, que le pipeline ne
    peut pas deviner.

**Un journal en écriture seule.**
    Chaque changement d'étape est consigné avec sa date, son auteur et son
    motif. Rien n'est écrasé : on peut toujours répondre à « qui a validé ce
    document, quand, et pourquoi ? ». C'est la contrepartie naturelle de la
    traçabilité exigée au §33 — jusque-là assurée sur le *contenu*, désormais
    étendue aux *décisions*.

**Des transitions contrôlées.**
    On ne valide pas un document qui n'a jamais été traité. Les passages
    permis sont déclarés dans :data:`ALLOWED_TRANSITIONS` et vérifiés ; une
    transition refusée lève une erreur explicite plutôt que de laisser le
    registre dans un état incohérent.

Le registre **ne valide jamais tout seul**. Il peut proposer une étape
(``a_verifier`` d'après le score qualité), jamais conclure : ``valide`` et
``rejete`` demandent un acteur humain nommé, conformément au §16.
"""

from __future__ import annotations

import enum
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

from bldp.logging_setup import get_logger
from bldp.models import Document, QualityStatus, ValidationStatus
from bldp.utils import utc_now_iso

logger = get_logger("tracking")


class TrackingError(RuntimeError):
    """Opération refusée par le registre de suivi."""


class Stage(str, enum.Enum):
    """Étapes du parcours d'un document, de l'import à l'archivage."""

    IMPORTE = "importe"
    EN_COURS = "en_cours"
    TRAITE = "traite"
    A_VERIFIER = "a_verifier"
    REVUE_IA = "revue_ia"
    EN_REVUE = "en_revue"
    VALIDE = "valide"
    REJETE = "rejete"
    ARCHIVE = "archive"


#: Transitions autorisées. Tout ce qui n'y figure pas est refusé.
#:
#: La table dit deux choses importantes. D'abord qu'on ne valide pas un
#: document qui n'a pas été traité : ``IMPORTE`` ne mène pas à ``VALIDE``.
#: Ensuite qu'une décision humaine reste révocable — ``VALIDE`` et ``REJETE``
#: peuvent revenir à ``A_VERIFIER``, car un juriste doit pouvoir rouvrir un
#: dossier sans qu'on ait à trafiquer la base.
ALLOWED_TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    # ``A_VERIFIER`` est joignable dès l'import : le pipeline importe et traite
    # dans la même exécution, et peut donc constater un problème avant même
    # d'être passé par ``TRAITE``. Sans ce passage, un document douteux restait
    # marqué « importé » et n'apparaissait dans aucune file de relecture.
    Stage.IMPORTE: frozenset(
        {Stage.EN_COURS, Stage.TRAITE, Stage.A_VERIFIER, Stage.REJETE}
    ),
    Stage.EN_COURS: frozenset({Stage.TRAITE, Stage.A_VERIFIER, Stage.REJETE, Stage.IMPORTE}),
    Stage.TRAITE: frozenset(
        {Stage.A_VERIFIER, Stage.REVUE_IA, Stage.EN_REVUE, Stage.VALIDE, Stage.REJETE}
    ),
    # « revue_ia » s'intercale entre le traitement et la decision humaine : un
    # document relu par un modele en sait plus qu'un document simplement
    # traite, et moins qu'un document valide par quelqu'un.
    Stage.A_VERIFIER: frozenset(
        {Stage.REVUE_IA, Stage.EN_REVUE, Stage.VALIDE, Stage.REJETE, Stage.TRAITE}
    ),
    Stage.REVUE_IA: frozenset(
        {Stage.EN_REVUE, Stage.VALIDE, Stage.REJETE, Stage.A_VERIFIER}
    ),
    Stage.EN_REVUE: frozenset(
        {Stage.VALIDE, Stage.REJETE, Stage.A_VERIFIER, Stage.REVUE_IA}
    ),
    Stage.VALIDE: frozenset({Stage.ARCHIVE, Stage.A_VERIFIER}),
    Stage.REJETE: frozenset({Stage.A_VERIFIER, Stage.ARCHIVE}),
    Stage.ARCHIVE: frozenset({Stage.A_VERIFIER}),
}

#: Étapes après lesquelles il n'y a plus de traitement automatique à faire.
#: Une reprise (``--resume``) s'appuie dessus pour ne pas refaire le travail.
SETTLED_STAGES: frozenset[Stage] = frozenset(
    {Stage.VALIDE, Stage.REJETE, Stage.ARCHIVE}
)

#: Étapes exigeant un acteur humain nommé (§16 : rien ne s'auto-valide).
HUMAN_ONLY_STAGES: frozenset[Stage] = frozenset({Stage.VALIDE, Stage.REJETE})

#: Badge affiché pour chaque étape : un marqueur et un libellé court.
#:
#: Les marqueurs sont **en ASCII** à dessein. Les pictogrammes géométriques
#: (``○``, ``●``, ``✓``) sont absents de cp1252, l'encodage par défaut d'une
#: console Windows : les afficher y interrompt la commande sur une
#: ``UnicodeEncodeError``. Un badge illisible sur la machine de l'utilisateur
#: ne vaut pas mieux qu'une absence de badge.
STAGE_BADGES: dict[Stage, tuple[str, str]] = {
    Stage.IMPORTE: ("[ ]", "importé"),
    Stage.EN_COURS: ("[~]", "en cours"),
    Stage.TRAITE: ("[*]", "traité"),
    Stage.A_VERIFIER: ("[!]", "à vérifier"),
    Stage.REVUE_IA: ("[IA]", "relu par IA"),
    Stage.EN_REVUE: ("[?]", "en revue"),
    Stage.VALIDE: ("[V]", "validé"),
    Stage.REJETE: ("[X]", "rejeté"),
    Stage.ARCHIVE: ("[-]", "archivé"),
}


def allowed_transitions(stage: Stage) -> frozenset[Stage]:
    """Étapes atteignables depuis ``stage``."""
    return ALLOWED_TRANSITIONS.get(stage, frozenset())


def badge_for(stage: Stage, priority: int = 0) -> str:
    """Badge lisible d'une étape, éventuellement marqué comme prioritaire."""
    symbol, label = STAGE_BADGES.get(stage, ("?", str(stage)))
    urgent = " !" if priority >= 2 else ""
    return f"{symbol} {label}{urgent}"


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


@dataclass
class TrackingEvent:
    """Une ligne du journal : un fait daté, jamais modifié."""

    ticket_id: str
    at: str
    actor: str
    action: str
    from_stage: Optional[str] = None
    to_stage: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "detail": self.detail,
        }


@dataclass
class Ticket:
    """Le suivi d'un document, de son import à sa validation."""

    ticket_id: str
    document_id: str
    file_hash: str
    filename: str = ""
    stage: Stage = Stage.IMPORTE
    assignee: Optional[str] = None
    priority: int = 0
    quality_score: Optional[float] = None
    title: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""
    events: list[TrackingEvent] = field(default_factory=list)

    @property
    def badge(self) -> str:
        return badge_for(self.stage, self.priority)

    @property
    def is_settled(self) -> bool:
        """Vrai si plus aucun traitement automatique n'est attendu."""
        return self.stage in SETTLED_STAGES

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "document_id": self.document_id,
            "file_hash": self.file_hash,
            "filename": self.filename,
            "stage": self.stage.value,
            "badge": self.badge,
            "assignee": self.assignee,
            "priority": self.priority,
            "quality_score": self.quality_score,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Schéma
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracking_tickets (
    ticket_id     TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    -- Une empreinte, un ticket : c'est ce qui empêche de retraiter deux fois
    -- le même contenu, quel que soit son nom de fichier.
    file_hash     TEXT NOT NULL UNIQUE,
    filename      TEXT,
    title         TEXT,
    stage         TEXT NOT NULL,
    assignee      TEXT,
    priority      INTEGER DEFAULT 0,
    quality_score REAL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    notes         TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tickets_stage ON tracking_tickets(stage);
CREATE INDEX IF NOT EXISTS idx_tickets_document ON tracking_tickets(document_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tracking_tickets(assignee);

-- Journal en écriture seule : on ajoute, on ne modifie jamais.
CREATE TABLE IF NOT EXISTS tracking_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    from_stage TEXT,
    to_stage   TEXT,
    detail     TEXT DEFAULT '',
    FOREIGN KEY (ticket_id) REFERENCES tracking_tickets(ticket_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_ticket ON tracking_events(ticket_id, at);
"""

#: Préfixe des identifiants de ticket.
TICKET_PREFIX = "BLDP"


# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------


class TrackingRegistry:
    """Accès au registre de suivi, adossé à la base SQLite du corpus.

    S'utilise comme gestionnaire de contexte ::

        with TrackingRegistry(config.path("database")) as registre:
            ticket = registre.open_ticket(document)
            registre.advance(ticket.ticket_id, Stage.EN_REVUE, actor="mv")
    """

    def __init__(self, path: str | Path, create: bool = True) -> None:
        self.path = Path(path)
        if not create and not self.path.exists():
            raise TrackingError(f"Registre introuvable : {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    # -- cycle de vie -------------------------------------------------------

    def __enter__(self) -> "TrackingRegistry":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    # -- création et lecture ------------------------------------------------

    def next_ticket_id(self) -> str:
        """Identifiant lisible et croissant : ``BLDP-000042``."""
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM tracking_tickets"
        ).fetchone()
        return f"{TICKET_PREFIX}-{row['n'] + 1:06d}"

    def find_by_hash(self, file_hash: str) -> Optional[Ticket]:
        """Ticket correspondant à une empreinte de contenu."""
        row = self.connection.execute(
            "SELECT * FROM tracking_tickets WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return _row_to_ticket(row) if row else None

    def get(self, ticket_id: str, with_history: bool = False) -> Optional[Ticket]:
        """Ticket par identifiant, éventuellement avec son journal."""
        row = self.connection.execute(
            "SELECT * FROM tracking_tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            return None
        ticket = _row_to_ticket(row)
        if with_history:
            ticket.events = self.history(ticket_id)
        return ticket

    def resolve(self, reference: str) -> Optional[Ticket]:
        """Retrouve un ticket par son identifiant **ou** par un document_id.

        Le suivi se consulte souvent en partant du document qu'on a sous les
        yeux, pas du numéro de ticket qu'on ne connaît pas par cœur.
        """
        ticket = self.get(reference)
        if ticket is not None:
            return ticket
        row = self.connection.execute(
            "SELECT * FROM tracking_tickets WHERE document_id = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (reference,),
        ).fetchone()
        return _row_to_ticket(row) if row else None

    def history(self, ticket_id: str) -> list[TrackingEvent]:
        """Journal complet d'un ticket, du plus ancien au plus récent."""
        return [
            TrackingEvent(
                ticket_id=row["ticket_id"],
                at=row["at"],
                actor=row["actor"],
                action=row["action"],
                from_stage=row["from_stage"],
                to_stage=row["to_stage"],
                detail=row["detail"] or "",
            )
            for row in self.connection.execute(
                "SELECT * FROM tracking_events WHERE ticket_id = ? ORDER BY id",
                (ticket_id,),
            )
        ]

    def list_tickets(
        self,
        stage: Optional[Stage] = None,
        assignee: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Ticket]:
        """Tickets filtrés par étape et/ou par personne assignée."""
        query = "SELECT * FROM tracking_tickets"
        clauses: list[str] = []
        params: list[object] = []
        if stage is not None:
            clauses.append("stage = ?")
            params.append(stage.value)
        if assignee is not None:
            clauses.append("assignee = ?")
            params.append(assignee)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority DESC, updated_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [_row_to_ticket(row) for row in self.connection.execute(query, params)]

    def counts_by_stage(self) -> dict[str, int]:
        """Nombre de tickets par étape, pour un tableau de bord."""
        return {
            row["stage"]: row["n"]
            for row in self.connection.execute(
                "SELECT stage, COUNT(*) AS n FROM tracking_tickets GROUP BY stage"
            )
        }

    # -- écriture -----------------------------------------------------------

    def open_ticket(
        self,
        document_id: str,
        file_hash: str,
        filename: str = "",
        title: Optional[str] = None,
        actor: str = "pipeline",
    ) -> Ticket:
        """Ouvre un ticket, ou renvoie celui qui existe déjà pour ce contenu.

        **Idempotent par empreinte.** Réimporter le même document ne crée pas
        un second ticket : il retrouve le sien, avec tout son historique et la
        décision humaine éventuellement déjà prise. C'est précisément ce qui
        évite de refaire un travail déjà fait.
        """
        existing = self.find_by_hash(file_hash)
        if existing is not None:
            if filename and filename != existing.filename:
                self._log(
                    existing.ticket_id,
                    actor,
                    "revu_sous_un_autre_nom",
                    detail=f"déjà suivi sous « {existing.filename} », revu comme « {filename} »",
                )
                logger.info(
                    "%s : contenu déjà suivi (%s) — aucun nouveau ticket.",
                    filename,
                    existing.ticket_id,
                )
            return existing

        now = utc_now_iso()
        ticket = Ticket(
            ticket_id=self.next_ticket_id(),
            document_id=document_id,
            file_hash=file_hash,
            filename=filename,
            title=title,
            stage=Stage.IMPORTE,
            created_at=now,
            updated_at=now,
        )
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO tracking_tickets (ticket_id, document_id, file_hash, "
                "filename, title, stage, assignee, priority, quality_score, "
                "created_at, updated_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 0, NULL, ?, ?, '')",
                (
                    ticket.ticket_id, ticket.document_id, ticket.file_hash,
                    ticket.filename, ticket.title, ticket.stage.value,
                    ticket.created_at, ticket.updated_at,
                ),
            )
        self._log(ticket.ticket_id, actor, "ouverture", to_stage=Stage.IMPORTE.value,
                  detail=f"document {document_id}")
        logger.info("Ticket %s ouvert pour %s.", ticket.ticket_id, document_id)
        return ticket

    def advance(
        self,
        ticket_id: str,
        to_stage: Stage,
        actor: str,
        reason: str = "",
    ) -> Ticket:
        """Fait passer un ticket à une nouvelle étape.

        Raises:
            TrackingError: transition interdite, ticket inconnu, ou étape
                réservée à une décision humaine demandée sans acteur nommé.
        """
        ticket = self.get(ticket_id)
        if ticket is None:
            raise TrackingError(f"Ticket inconnu : {ticket_id}")

        if to_stage is ticket.stage:
            return ticket

        permitted = allowed_transitions(ticket.stage)
        if to_stage not in permitted:
            lisible = ", ".join(sorted(s.value for s in permitted)) or "aucune"
            raise TrackingError(
                f"{ticket_id} : passage de « {ticket.stage.value} » à "
                f"« {to_stage.value} » interdit. Étapes possibles : {lisible}."
            )

        if to_stage in HUMAN_ONLY_STAGES and actor in ("", "pipeline", "auto"):
            raise TrackingError(
                f"« {to_stage.value} » est une décision humaine : indiquez qui "
                "la prend (--par <personne>). Le pipeline ne valide jamais "
                "de lui-même."
            )

        now = utc_now_iso()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE tracking_tickets SET stage = ?, updated_at = ? WHERE ticket_id = ?",
                (to_stage.value, now, ticket_id),
            )
        self._log(ticket_id, actor, "changement_etape", from_stage=ticket.stage.value,
                  to_stage=to_stage.value, detail=reason)
        logger.info(
            "%s : %s -> %s (par %s)", ticket_id, ticket.stage.value, to_stage.value, actor
        )
        ticket.stage = to_stage
        ticket.updated_at = now
        return ticket

    def assign(self, ticket_id: str, assignee: Optional[str], actor: str) -> Ticket:
        """Confie un ticket à quelqu'un — ou le libère si ``assignee`` est vide."""
        ticket = self.get(ticket_id)
        if ticket is None:
            raise TrackingError(f"Ticket inconnu : {ticket_id}")

        now = utc_now_iso()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE tracking_tickets SET assignee = ?, updated_at = ? WHERE ticket_id = ?",
                (assignee, now, ticket_id),
            )
        action = "assignation" if assignee else "liberation"
        self._log(ticket_id, actor, action,
                  detail=f"confié à {assignee}" if assignee else "rendu disponible")
        ticket.assignee = assignee
        ticket.updated_at = now
        return ticket

    def set_priority(self, ticket_id: str, priority: int, actor: str) -> Ticket:
        """Fixe la priorité (0 = normale, 1 = haute, 2 = urgente)."""
        ticket = self.get(ticket_id)
        if ticket is None:
            raise TrackingError(f"Ticket inconnu : {ticket_id}")
        priority = max(0, min(2, int(priority)))
        with self._transaction() as connection:
            connection.execute(
                "UPDATE tracking_tickets SET priority = ?, updated_at = ? WHERE ticket_id = ?",
                (priority, utc_now_iso(), ticket_id),
            )
        self._log(ticket_id, actor, "priorite", detail=f"priorité fixée à {priority}")
        ticket.priority = priority
        return ticket

    def annotate(self, ticket_id: str, note: str, actor: str) -> Ticket:
        """Ajoute une note libre, consignée au journal."""
        ticket = self.get(ticket_id)
        if ticket is None:
            raise TrackingError(f"Ticket inconnu : {ticket_id}")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE tracking_tickets SET notes = ?, updated_at = ? WHERE ticket_id = ?",
                (note, utc_now_iso(), ticket_id),
            )
        self._log(ticket_id, actor, "note", detail=note)
        ticket.notes = note
        return ticket

    # -- synchronisation avec le pipeline ------------------------------------

    def record_processing(self, document: Document, actor: str = "pipeline") -> Ticket:
        """Consigne le passage d'un document dans le pipeline.

        L'étape proposée découle de ce que le pipeline a constaté : un
        document en erreur part en ``a_verifier``, un document au contrôle
        qualité douteux aussi, les autres passent à ``traite``. Le registre
        **suggère** ; il ne conclut pas. Aucun chemin ne mène ici à
        ``valide`` : cette décision appartient à un humain (§16).
        """
        source = document.source
        ticket = self.open_ticket(
            document_id=document.document_id,
            file_hash=source.file_hash,
            filename=source.filename,
            title=document.metadata.title if document.metadata else None,
            actor=actor,
        )

        score = document.quality.score if document.quality else None
        cible, motif = _suggest_stage(document)

        # Une décision humaine déjà prise n'est jamais défaite par une
        # nouvelle exécution : on enregistre le passage, sans toucher l'étape.
        if ticket.is_settled or ticket.stage in {Stage.EN_REVUE, Stage.REVUE_IA}:
            self._update_quality(ticket.ticket_id, score, document)
            self._log(
                ticket.ticket_id, actor, "retraitement",
                detail=f"pipeline rejoué ; étape « {ticket.stage.value} » conservée",
            )
            return self.get(ticket.ticket_id) or ticket

        self._update_quality(ticket.ticket_id, score, document)
        if cible is ticket.stage:
            self._log(ticket.ticket_id, actor, "traitement", detail=motif)
            return self.get(ticket.ticket_id) or ticket

        if cible in allowed_transitions(ticket.stage):
            return self.advance(ticket.ticket_id, cible, actor, motif)

        # L'étape suggérée n'est pas joignable depuis l'étape courante. On ne
        # force rien — mais on ne se tait pas non plus : un ticket bloqué sans
        # trace serait invisible dans toutes les files de relecture.
        self._log(
            ticket.ticket_id, actor, "suggestion_non_appliquee",
            from_stage=ticket.stage.value, detail=(
                f"le traitement suggère « {cible.value} » ({motif}), "
                f"mais cette étape n'est pas joignable depuis "
                f"« {ticket.stage.value} »"
            ),
        )
        logger.warning(
            "%s : étape « %s » suggérée mais non joignable depuis « %s ».",
            ticket.ticket_id, cible.value, ticket.stage.value,
        )
        return self.get(ticket.ticket_id) or ticket

    def record_batch(
        self, documents: Sequence[Document], actor: str = "pipeline"
    ) -> list[Ticket]:
        """Consigne un lot entier ; un document en échec n'arrête pas les autres."""
        tickets: list[Ticket] = []
        for document in documents:
            try:
                tickets.append(self.record_processing(document, actor))
            except Exception as exc:  # noqa: BLE001 — §26
                logger.warning(
                    "Suivi impossible pour %s : %s", document.document_id, exc
                )
        return tickets

    def settled_hashes(self) -> dict[str, str]:
        """Empreintes dont le sort est déjà réglé : ``{empreinte: ticket}``.

        Sert à la reprise : inutile de retraiter un document qu'un humain a
        déjà validé, rejeté ou archivé.
        """
        marks = ",".join("?" for _ in SETTLED_STAGES)
        return {
            row["file_hash"]: row["ticket_id"]
            for row in self.connection.execute(
                f"SELECT file_hash, ticket_id FROM tracking_tickets "
                f"WHERE stage IN ({marks})",
                [s.value for s in SETTLED_STAGES],
            )
        }

    # -- interne ------------------------------------------------------------

    def _update_quality(
        self, ticket_id: str, score: Optional[float], document: Document
    ) -> None:
        """Reporte le score qualité et en déduit une priorité de relecture."""
        priority = 0
        if document.errors:
            priority = 2
        elif score is not None and score < 0.75:
            priority = 1
        with self._transaction() as connection:
            connection.execute(
                "UPDATE tracking_tickets SET quality_score = ?, priority = ?, "
                "document_id = ?, updated_at = ? WHERE ticket_id = ?",
                (score, priority, document.document_id, utc_now_iso(), ticket_id),
            )

    def _log(
        self,
        ticket_id: str,
        actor: str,
        action: str,
        from_stage: Optional[str] = None,
        to_stage: Optional[str] = None,
        detail: str = "",
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO tracking_events (ticket_id, at, actor, action, "
                "from_stage, to_stage, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticket_id, utc_now_iso(), actor or "inconnu", action,
                 from_stage, to_stage, detail),
            )


# ---------------------------------------------------------------------------
# Fonctions libres
# ---------------------------------------------------------------------------


def _suggest_stage(document: Document) -> tuple[Stage, str]:
    """Étape suggérée après passage du pipeline, avec sa justification."""
    if document.errors:
        return Stage.A_VERIFIER, f"traitement en échec : {document.errors[0]}"
    if document.validation is ValidationStatus.TO_REVIEW:
        return Stage.A_VERIFIER, "le contrôle qualité demande une vérification"
    if document.quality and document.quality.status is not QualityStatus.OK:
        return Stage.A_VERIFIER, f"qualité « {document.quality.status.value} »"
    return Stage.TRAITE, "traitement automatique terminé sans réserve"


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        ticket_id=row["ticket_id"],
        document_id=row["document_id"],
        file_hash=row["file_hash"],
        filename=row["filename"] or "",
        title=row["title"],
        stage=Stage(row["stage"]),
        assignee=row["assignee"],
        priority=row["priority"] or 0,
        quality_score=row["quality_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        notes=row["notes"] or "",
    )
