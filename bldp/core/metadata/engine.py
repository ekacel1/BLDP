"""Module 7 — extraction des métadonnées documentaires (§12).

Les métadonnées sont déduites de trois sources, par ordre de fiabilité
décroissante :

1. un fichier ``<document>.meta.yaml`` ou ``.meta.json`` déposé à côté du PDF —
   c'est la voie *manuelle*, toujours prioritaire, qui permet à un juriste de
   corriger définitivement une valeur ;
2. le texte des premières pages (titre, numéro officiel, date, autorité) ;
3. les métadonnées du conteneur PDF et le nom du fichier — indices faibles.

Chaque champ deviné est accompagné d'un **score de confiance** et de la
**preuve** qui l'a produit (``evidence``), de sorte que le pipeline puisse
toujours répondre à « d'où vient cette date ? ». Conformément au §33, aucune
valeur n'est inventée : lorsqu'une information est introuvable, le champ reste
vide et un avertissement est émis.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import (
    DocumentMetadata,
    DocumentType,
    LegalStatus,
    Page,
    SourceFile,
)
from bldp.jurisdictions.registry import JurisdictionProfile, get_profile
from bldp.utils import read_json, today_iso

logger = get_logger("metadata")

#: Mois français -> numéro, pour normaliser les dates en ISO-8601.
MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "aout": 8, "août": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
}

#: Type déduit du sous-dossier d'importation (indice faible mais utile).
CATEGORY_TO_TYPE = {
    "lois": DocumentType.LOI,
    "codes": DocumentType.CODE,
    "decrets": DocumentType.DECRET,
    "arretes": DocumentType.ARRETE,
    "jurisprudence": DocumentType.JURISPRUDENCE,
}

#: Domaines juridiques reconnus par mots-clés (indicatif, jamais bloquant).
LEGAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "travail": ("travail", "salarie", "salarié", "employeur", "contrat de travail", "syndicat"),
    "penal": ("penal", "pénal", "infraction", "delit", "délit", "peine", "emprisonnement"),
    "civil": ("civil", "mariage", "succession", "propriete", "propriété", "obligations"),
    "commercial": ("commerce", "commercial", "societe", "société", "ohada", "entreprise"),
    "fiscal": ("fiscal", "impot", "impôt", "taxe", "douane", "contribution"),
    "administratif": ("administratif", "fonction publique", "collectivite", "collectivité"),
    "foncier": ("foncier", "domanial", "terrain", "cadastre", "propriete fonciere"),
    "electoral": ("electoral", "électoral", "election", "élection", "scrutin"),
    "constitutionnel": ("constitution", "constitutionnel"),
    "famille": ("famille", "personnes", "filiation", "divorce"),
    "environnement": ("environnement", "pollution", "eaux", "forets", "forêts"),
    "numerique": ("numerique", "numérique", "donnees personnelles", "données personnelles"),
}

#: Extensions acceptées pour un fichier de métadonnées déposé manuellement.
SIDECAR_SUFFIXES = (".meta.yaml", ".meta.yml", ".meta.json")

#: Mention introduisant l'objet d'un texte : « portant … », « relative à … ».
#: Recherchée **n'importe où** dans la ligne : le nettoyage recolle les retours
#: à la ligne artificiels, si bien que « LOI N° 2026-001 » et « portant … » se
#: retrouvent souvent fusionnés sur une seule ligne.
TITLE_KEYWORD_RE = re.compile(
    r"\b(?:portant|relatives?\s+[àa]|relatif\s+[àa]|fixant|instituant|"
    r"modifiant|compl[ée]tant|abrogeant|cr[ée]ant|organisant)\b",
    re.IGNORECASE,
)

#: Début d'article : au-delà, on ne cherche plus d'intitulé de document.
_ARTICLE_START_RE = re.compile(r"^\s*(?:article|art\.)\s", re.IGNORECASE)


def _mentions_number(line: str, number: str) -> bool:
    """Vrai si la ligne contient le numéro officiel, quelle que soit sa forme."""
    compact = re.sub(r"[\s\-–]", "", line)
    return re.sub(r"[\s\-–]", "", number) in compact


# ---------------------------------------------------------------------------
# Métadonnées fournies manuellement
# ---------------------------------------------------------------------------


def find_sidecar(pdf_path: str | Path) -> Optional[Path]:
    """Cherche un fichier de métadonnées à côté du document."""
    base = Path(pdf_path)
    for suffix in SIDECAR_SUFFIXES:
        candidate = base.with_suffix("").with_name(base.stem + suffix)
        if candidate.exists():
            return candidate
    return None


def load_sidecar(path: str | Path) -> dict:
    """Lit un fichier de métadonnées manuel (YAML ou JSON)."""
    target = Path(path)
    if target.suffix.lower() == ".json":
        data = read_json(target)
    else:
        import yaml

        with target.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{target} doit contenir un dictionnaire de métadonnées.")
    return data


# ---------------------------------------------------------------------------
# Détection dans le texte
# ---------------------------------------------------------------------------


def header_text(pages: Sequence[Page], page_count: int = 2) -> str:
    """Texte des premières pages, où figurent titre, numéro et date."""
    return "\n".join(page.text for page in pages[:page_count])


def normalize_date(day: str, month: str | None, year: str, month_num: str | None = None) -> Optional[str]:
    """Compose une date ISO-8601 à partir de ses composantes textuelles.

    Renvoie ``None`` si la date est incohérente : on préfère l'absence de date
    à une date fausse.
    """
    try:
        day_value = int(day)
        year_value = int(year)
        if month_num is not None:
            month_value = int(month_num)
        else:
            month_value = MONTHS.get((month or "").strip().lower(), 0)
    except (TypeError, ValueError):
        return None

    if not (1 <= day_value <= 31 and 1 <= month_value <= 12 and 1500 <= year_value <= 2200):
        return None
    return f"{year_value:04d}-{month_value:02d}-{day_value:02d}"


def detect_date(text: str, profile: JurisdictionProfile | None) -> tuple[Optional[str], float, str]:
    """Repère la date de signature du texte.

    Returns:
        ``(date_iso, confiance, preuve)``. La forme « du 10 février 2026 » est
        la plus fiable : c'est celle qui figure dans l'intitulé officiel.
    """
    patterns = profile.date_patterns if profile else []
    for rank, pattern in enumerate(patterns):
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        iso = normalize_date(
            groups.get("day", ""),
            groups.get("month"),
            groups.get("year", ""),
            groups.get("month_num"),
        )
        if iso:
            # Le premier motif (« du <date> ») est le plus sûr.
            confidence = 0.95 if rank == 0 else max(0.60, 0.90 - 0.15 * rank)
            return iso, confidence, match.group(0).strip()
    return None, 0.0, ""


def detect_number(text: str, profile: JurisdictionProfile | None) -> tuple[Optional[str], float, str]:
    """Repère le numéro officiel du texte (« 2026-001 »)."""
    for rank, pattern in enumerate(profile.number_patterns if profile else []):
        match = pattern.search(text)
        if match:
            number = re.sub(r"\s+", "", match.group("number"))
            number = number.replace("–", "-")
            return number, 0.92 if rank == 0 else 0.75, match.group(0).strip()
    return None, 0.0, ""


def detect_document_type(
    text: str,
    profile: JurisdictionProfile | None,
    category: str = "autres",
    filename: str = "",
) -> tuple[DocumentType, float, str]:
    """Détermine la nature du texte.

    Le texte prime sur le classement en dossier, qui prime sur le nom de
    fichier. Si rien ne ressort, on renvoie ``INCONNU`` : le §33 interdit de
    deviner un type qui influencerait ensuite l'interprétation juridique.
    """
    if profile:
        # Un texte cite presque toujours d'autres textes : un décret « portant
        # application de la loi n° 2026-001 » contient le motif d'une loi. Or
        # l'intitulé d'un document précède ses citations. On retient donc le
        # motif dont la correspondance apparaît **le plus tôt** dans le texte,
        # et non le premier type déclaré — sans quoi une simple référence
        # requalifierait le document, avec les conséquences juridiques que cela
        # implique.
        best: tuple[int, int, str, str] | None = None
        for rank, (type_name, patterns) in enumerate(
            profile.document_type_patterns.items()
        ):
            for pattern in patterns:
                match = pattern.search(text)
                if match and (best is None or (match.start(), rank) < (best[0], best[1])):
                    best = (match.start(), rank, type_name, match.group(0).strip()[:80])

        if best is not None:
            _, _, type_name, evidence = best
            return DocumentType(type_name), 0.90, evidence

    if category in CATEGORY_TO_TYPE:
        return CATEGORY_TO_TYPE[category], 0.45, f"classement manuel dans input/{category}/"

    lowered = filename.lower()
    for keyword, doc_type in (
        ("constitution", DocumentType.CONSTITUTION),
        ("code", DocumentType.CODE),
        ("loi", DocumentType.LOI),
        ("decret", DocumentType.DECRET),
        ("arrete", DocumentType.ARRETE),
        ("ordonnance", DocumentType.ORDONNANCE),
    ):
        if keyword in lowered:
            return doc_type, 0.35, f"nom de fichier : {filename}"

    return DocumentType.INCONNU, 0.0, ""


def detect_authority(text: str, profile: JurisdictionProfile | None) -> tuple[Optional[str], float, str]:
    """Repère l'autorité émettrice."""
    for name, patterns in (profile.authority_patterns if profile else {}).items():
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return name, 0.80, match.group(0).strip()
    return None, 0.0, ""


def detect_title(
    text: str,
    document_type: DocumentType,
    number: Optional[str],
) -> tuple[Optional[str], float, str]:
    """Reconstitue l'intitulé du texte.

    On privilégie la ligne « portant … » / « relative à … », qui constitue
    l'intitulé officiel. À défaut, la première ligne substantielle des
    premières pages sert de titre, avec une confiance faible.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    for index, line in enumerate(lines[:25]):
        if _ARTICLE_START_RE.match(line):
            break  # au-delà du premier article, plus d'intitulé à trouver
        match = TITLE_KEYWORD_RE.search(line)
        if not match:
            continue

        if line[: match.start()].strip():
            # La mention « portant … » est en milieu de ligne : l'intitulé
            # officiel et le numéro ont été fusionnés (le nettoyage recolle les
            # retours à la ligne artificiels). La ligne porte déjà tout.
            return _tidy_title(line), 0.85, line[:80]

        # La mention commence la ligne : le numéro est sur la ligne précédente.
        preceding = lines[index - 1] if index else ""
        if number and _mentions_number(preceding, number):
            return _tidy_title(f"{preceding} {line}"), 0.85, line[:80]
        return _tidy_title(line), 0.85, line[:80]

    for line in lines[:12]:
        if len(line) < 12 or line.isdigit():
            continue
        if re.match(r"^r[ée]publique\s+du\b", line, re.IGNORECASE):
            continue
        return _tidy_title(line), 0.40, "première ligne substantielle"

    return None, 0.0, ""


def _tidy_title(title: str) -> str:
    """Nettoie un intitulé : espaces, capitales criardes, ponctuation finale."""
    cleaned = re.sub(r"\s+", " ", title).strip(" .;:—–-")
    letters = [c for c in cleaned if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.85:
        cleaned = cleaned.capitalize()
    return cleaned[:400]


def detect_legal_domain(text: str) -> tuple[Optional[str], float, str]:
    """Devine le domaine juridique par comptage de mots-clés.

    Purement indicatif : la confiance reste volontairement basse, et le champ
    n'est jamais utilisé pour une décision automatique.
    """
    lowered = text.lower()
    scores: dict[str, int] = {}
    for domain, keywords in LEGAL_DOMAINS.items():
        hits = sum(lowered.count(keyword) for keyword in keywords)
        if hits:
            scores[domain] = hits

    if not scores:
        return None, 0.0, ""

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    # Un domaine n'est retenu que s'il domine nettement le suivant.
    confidence = 0.55 if best_score >= max(3, runner_up * 2) else 0.30
    return best, confidence, f"{best_score} occurrence(s) de mots-clés « {best} »"


def detect_status(text: str, profile: JurisdictionProfile | None) -> tuple[LegalStatus, float, str]:
    """Statut juridique déclaré par le texte lui-même.

    Un document ne déclare presque jamais son propre statut : l'absence de
    signal donne ``INCONNU`` plutôt que ``EN_VIGUEUR``, qui serait une
    supposition dangereuse (§13).
    """
    for status_name, patterns in (profile.status_patterns if profile else {}).items():
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return LegalStatus(status_name), 0.50, match.group(0).strip()
    return LegalStatus.INCONNU, 0.0, ""


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------


def extract_metadata(
    document_id: str,
    pages: Sequence[Page],
    config: Config,
    source: SourceFile | None = None,
    pdf_metadata: dict[str, Any] | None = None,
    profile: JurisdictionProfile | None = None,
) -> DocumentMetadata:
    """Construit les métadonnées d'un document (§12).

    Args:
        document_id: identifiant du document.
        pages: pages nettoyées.
        config: configuration (section ``metadata``).
        source: enregistrement d'importation (catégorie, chemin, dates).
        pdf_metadata: métadonnées du conteneur PDF, indices faibles.
        profile: profil de juridiction ; par défaut celui de la configuration.

    Returns:
        Les métadonnées, chaque champ deviné portant sa confiance et sa preuve.
    """
    profile = profile or get_profile(config)
    header_pages = int(config.get("metadata.header_pages", 2))
    text = header_text(pages, header_pages)
    full_text = "\n".join(page.text for page in pages)

    metadata = DocumentMetadata(
        document_id=document_id,
        jurisdiction=str(config.get("metadata.default_jurisdiction", "Benin")),
        language=str(config.get("project.language", "fr")),
        retrieved_at=source.ingested_at[:10] if source else today_iso(),
    )

    def record(field: str, value: Any, confidence: float, evidence: str) -> None:
        if value in (None, "", DocumentType.INCONNU, LegalStatus.INCONNU):
            return
        setattr(metadata, field, value)
        metadata.confidence[field] = round(confidence, 2)
        if evidence:
            metadata.evidence[field] = evidence

    doc_type, type_conf, type_evidence = detect_document_type(
        text,
        profile,
        category=source.category if source else "autres",
        filename=source.filename if source else "",
    )
    record("type", doc_type, type_conf, type_evidence)

    number, number_conf, number_evidence = detect_number(text, profile)
    record("number", number, number_conf, number_evidence)

    date_iso, date_conf, date_evidence = detect_date(text, profile)
    record("date", date_iso, date_conf, date_evidence)

    title, title_conf, title_evidence = detect_title(text, doc_type, number)
    record("title", title, title_conf, title_evidence)

    authority, authority_conf, authority_evidence = detect_authority(text, profile)
    record("authority", authority, authority_conf, authority_evidence)

    domain, domain_conf, domain_evidence = detect_legal_domain(full_text)
    record("legal_domain", domain, domain_conf, domain_evidence)

    status, status_conf, status_evidence = detect_status(full_text, profile)
    record("status", status, status_conf, status_evidence)

    # Repli sur les métadonnées du conteneur PDF, avec confiance minimale :
    # elles sont souvent héritées du logiciel de numérisation.
    if not metadata.title and pdf_metadata and pdf_metadata.get("pdf_title"):
        record("title", _tidy_title(pdf_metadata["pdf_title"]), 0.25, "métadonnée PDF")

    if source:
        metadata.source = str(config.get("metadata.default_source", "inconnu"))
        metadata.evidence["source_path"] = source.source_path

    # Surcharge manuelle : toujours prioritaire, confiance maximale (§16).
    if source:
        sidecar = find_sidecar(source.source_path)
        if sidecar:
            try:
                apply_manual_metadata(metadata, load_sidecar(sidecar), str(sidecar))
            except (OSError, ValueError) as exc:
                metadata.warnings.append(f"fichier de métadonnées illisible ({sidecar}) : {exc}")
                logger.warning("Métadonnées manuelles ignorées pour %s : %s", document_id, exc)

    _flag_missing(metadata, config)
    logger.info(
        "%s : type=%s, numéro=%s, date=%s, autorité=%s",
        document_id,
        metadata.type.value,
        metadata.number or "?",
        metadata.date or "?",
        metadata.authority or "?",
    )
    return metadata


def apply_manual_metadata(
    metadata: DocumentMetadata,
    overrides: dict,
    origin: str = "saisie manuelle",
) -> DocumentMetadata:
    """Applique des métadonnées saisies par un humain.

    Les valeurs manuelles écrasent toujours les valeurs déduites et reçoivent
    une confiance de 1.0 : c'est le mécanisme de correction prévu par le §16.
    """
    aliases = {"titre": "title", "numero": "number", "autorite": "authority",
               "domaine_juridique": "legal_domain", "statut": "status", "langue": "language"}

    for raw_key, value in overrides.items():
        key = aliases.get(raw_key, raw_key)
        if value in (None, "") or not hasattr(metadata, key):
            continue
        if key == "type":
            try:
                value = DocumentType(str(value).lower())
            except ValueError:
                metadata.warnings.append(f"type de document inconnu ignoré : {value!r}")
                continue
        elif key == "status":
            try:
                value = LegalStatus(str(value).lower())
            except ValueError:
                metadata.warnings.append(f"statut juridique inconnu ignoré : {value!r}")
                continue
        elif key in {"confidence", "evidence", "warnings"}:
            continue
        setattr(metadata, key, value)
        metadata.confidence[key] = 1.0
        metadata.evidence[key] = origin
    return metadata


def _flag_missing(metadata: DocumentMetadata, config: Config) -> None:
    """Signale les métadonnées minimales absentes, sans jamais les inventer."""
    guess = bool(config.get("metadata.guess_missing", False))
    for field, label in (
        ("title", "titre"),
        ("type", "type de document"),
        ("number", "numéro officiel"),
        ("date", "date"),
        ("authority", "autorité émettrice"),
    ):
        value = getattr(metadata, field)
        missing = value in (None, "") or value is DocumentType.INCONNU
        if missing:
            metadata.warnings.append(f"{label} introuvable — saisie manuelle recommandée")

    weak = [
        field
        for field, score in metadata.confidence.items()
        if score < 0.50 and field not in {"legal_domain", "status"}
    ]
    if weak:
        metadata.warnings.append(
            "champ(s) de faible confiance à vérifier : " + ", ".join(sorted(weak))
        )
    if guess:
        metadata.warnings.append(
            "metadata.guess_missing est activé : des valeurs peuvent avoir été supposées"
        )


def metadata_completeness(metadata: DocumentMetadata) -> float:
    """Part des métadonnées minimales du §12 effectivement renseignées."""
    required = ("title", "type", "number", "date", "jurisdiction", "language", "source")
    filled = 0
    for field in required:
        value = getattr(metadata, field, None)
        if value not in (None, "", DocumentType.INCONNU):
            filled += 1
    return round(filled / len(required), 4)


def iter_missing_fields(metadata: DocumentMetadata) -> Iterable[str]:
    """Champs minimaux encore vides, pour l'interface de validation."""
    for field in ("title", "type", "number", "date", "authority", "source_url"):
        value = getattr(metadata, field, None)
        if value in (None, "") or value is DocumentType.INCONNU:
            yield field
