"""Utilitaires transverses : hachage, identifiants, dates, entrées/sorties JSON.

Ces fonctions sont volontairement sans dépendance sur le reste du package afin
de pouvoir être réutilisées par n'importe quel module (et testées isolément).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

#: Taille de bloc pour le hachage des fichiers volumineux (1 Mio).
_CHUNK_SIZE = 1024 * 1024

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")

#: Nombre romain **canonique**, et non n'importe quelle suite de lettres
#: romaines.
#:
#: L'OCR produit des séquences comme ``ICI``, ``vlII`` ou ``XVX``. Une lecture
#: permissive leur attribuait une valeur — ``ICI`` valait 100 — ce qui créait
#: ensuite de fausses ruptures de numérotation. Mieux vaut refuser d'interpréter
#: et le signaler (§33) que produire un rang inventé.
_ROMAN_RE = re.compile(
    r"^M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)

#: Tous les tirets Unicode rencontrés en sortie d'OCR.
#:
#: Ce n'est pas de la coquetterie typographique. Sur des scans réels, Tesseract
#: rend le tiret de « LOI n° 2025 — 18 » par un **cadratin** (U+2014). Un motif
#: n'acceptant que ``-`` et ``–`` ne reconnaissait donc pas le numéro propre du
#: document, et retenait à la place celui du texte *cité* dans l'intitulé
#: (« modifiant la loi n° 2022-09 ») — la loi se retrouvait identifiée sous le
#: numéro d'une autre.
DASHES = "-‐‑‒–—―−︱︲﹘﹣－"

#: Classe de caractères prête à l'emploi dans une expression régulière.
DASH_CLASS = "[" + re.escape(DASHES) + "]"

_DASH_RE = re.compile(DASH_CLASS)
#: Tiret séparant deux nombres, éventuellement entouré d'espaces.
_DASH_BETWEEN_DIGITS_RE = re.compile(rf"(?<=\d)\s*{DASH_CLASS}\s*(?=\d)")


#: Caractères par lesquels l'OCR rend le symbole « ° » de « n° ».
#:
#: Sur des scans médiocres, Tesseract lit « N° 2019 » comme « N" 2019 »,
#: « N' 2019 » ou « N. 2019 ». Un motif n'acceptant que ``°`` ne reconnaît
#: alors aucun numéro officiel — et sans numéro, la date et le type se
#: rabattent sur les **visas**, c'est-à-dire sur les textes *cités*. Une
#: seule lettre mal lue suffisait à faire dériver toutes les métadonnées.
DEGREE_MARKS = "°ºoO˚'\"’‘”“*•.,:;"

#: Préfixe « n° » tolérant à l'OCR, à insérer dans une expression régulière.
#: ``\b`` empêche de mordre sur le « n » de « en » ou « an ».
NUMERO_PREFIX = r"\bn" + f"[{re.escape(DEGREE_MARKS)}]" + r"*\s*"

#: Chiffres et leurs confusions OCR courantes (O/o/Q pour 0, l/I/| pour 1).
#: N'a de sens que dans un contexte déjà identifié comme numérique.
OCR_DIGIT = r"[0-9OoQlI|]"

_OCR_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "Q": "0", "l": "1", "I": "1", "|": "1"})

#: Séparateurs de numérotation produits par l'OCR : tirets… et l'underscore,
#: par lequel Tesseract rend parfois un tiret sur fond bruité.
_NUMBER_SEPARATORS = DASHES + "_"
_SEPARATOR_BETWEEN_DIGITS_RE = re.compile(
    rf"(?<=\d)\s*[{re.escape(_NUMBER_SEPARATORS)}]\s*(?=\d)"
)


#: Séparateurs admis **dans un numéro déjà capturé**, point compris.
#:
#: Le point ne figure pas dans :data:`_NUMBER_SEPARATORS` — qui s'applique au
#: texte entier — car il y transformerait les dates « 11.12.1990 » et les
#: décimales. Restreint au numéro, il est sans risque.
_CAPTURED_SEPARATORS = _NUMBER_SEPARATORS + "."
_CAPTURED_SEPARATOR_RUN_RE = re.compile(f"[{re.escape(_CAPTURED_SEPARATORS)}]+")


def normalize_ocr_number(value: str) -> str:
    """Nettoie un numéro officiel capturé sur du texte OCRisé.

    Ramène les lettres confondues avec des chiffres (« 2018-OO1 » → « 2018-001 »)
    et uniformise le séparateur, y compris quand l'OCR en produit plusieurs à la
    suite (« 2010.- 028 » → « 2010-028 »). À n'appliquer qu'à une valeur **déjà
    reconnue comme un numéro** : hors de ce contexte, transformer un « O » en
    zéro corromprait le texte.
    """
    compact = re.sub(r"\s+", "", value)
    compact = _CAPTURED_SEPARATOR_RUN_RE.sub("-", compact)
    # Le suffixe littéral (« /PR/SGG ») n'est pas numérique : on le préserve.
    head, slash, tail = compact.partition("/")
    return head.translate(_OCR_DIGIT_FIXES) + slash + tail


def normalize_dashes(text: str) -> str:
    """Ramène tout tiret Unicode au trait d'union ASCII."""
    return _DASH_RE.sub("-", text)


def normalize_number_separators(text: str) -> str:
    """Normalise tiret **ou underscore** entre chiffres (alias moderne)."""
    return _SEPARATOR_BETWEEN_DIGITS_RE.sub("-", text)


def normalize_number_dashes(text: str) -> str:
    """Normalise les tirets **entre chiffres** uniquement.

    Volontairement plus étroit que :func:`normalize_dashes` : un cadratin
    d'incise (« la loi — adoptée en 2025 — dispose ») est de la ponctuation, et
    le remplacer altérerait le texte. Entre deux chiffres, en revanche, le
    tiret est un séparateur de numérotation : le normaliser est sans risque et
    rend les numéros officiels reconnaissables.
    """
    return _SEPARATOR_BETWEEN_DIGITS_RE.sub("-", text)

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

#: Ordinaux littéraux rencontrés dans les textes juridiques français.
_WORD_ORDINALS = {
    "premier": 1, "premiere": 1, "première": 1, "1er": 1, "1ere": 1, "1ère": 1,
    "deuxieme": 2, "deuxième": 2, "second": 2, "seconde": 2, "2eme": 2, "2ème": 2,
    "troisieme": 3, "troisième": 3,
    "quatrieme": 4, "quatrième": 4,
    "cinquieme": 5, "cinquième": 5,
    "sixieme": 6, "sixième": 6,
    "septieme": 7, "septième": 7,
    "huitieme": 8, "huitième": 8,
    "neuvieme": 9, "neuvième": 9,
    "dixieme": 10, "dixième": 10,
}

#: Suffixes d'articles ordonnés (« 45 bis » vient après « 45 »).
_SUFFIX_ORDER = {
    "": 0, "bis": 1, "ter": 2, "quater": 3, "quinquies": 4, "sexies": 5,
    "septies": 6, "octies": 7, "nonies": 8, "decies": 9,
}


# ---------------------------------------------------------------------------
# Temps
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Horodatage ISO-8601 en UTC, à la seconde."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_iso() -> str:
    """Date du jour au format ``AAAA-MM-JJ``."""
    return datetime.now(timezone.utc).date().isoformat()


def timestamp_slug() -> str:
    """Horodatage compact utilisable dans un nom de fichier."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Hachage
# ---------------------------------------------------------------------------


def hash_file(path: str | Path, algorithm: str = "sha256") -> str:
    """SHA-256 du contenu binaire d'un fichier (lecture par blocs)."""
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_text(text: str, algorithm: str = "sha256") -> str:
    """Hachage d'un texte **normalisé**.

    La normalisation (casse, espaces, accents composés) permet de reconnaître
    deux extractions du même document malgré des différences de mise en forme.
    """
    normalized = normalize_for_hash(text)
    return hashlib.new(algorithm, normalized.encode("utf-8")).hexdigest()


def normalize_for_hash(text: str) -> str:
    """Forme canonique d'un texte pour la comparaison de doublons."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Identifiants
# ---------------------------------------------------------------------------


def slugify(value: str, max_length: int = 80) -> str:
    """Transforme un texte libre en identifiant ``snake_case`` ASCII."""
    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_INVALID.sub("_", ascii_text).strip("_")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("_")
    return slug or "document"


def make_document_id(filename: str, file_hash: str, existing: Iterable[str] = ()) -> str:
    """Identifiant stable et lisible pour un document.

    Basé sur le nom de fichier ; en cas de collision avec un identifiant déjà
    attribué, on suffixe par les 8 premiers caractères du hash (déterministe,
    donc reproductible d'une exécution à l'autre).
    """
    base = slugify(Path(filename).stem)
    existing = set(existing)
    if base not in existing:
        return base
    candidate = f"{base}_{file_hash[:8]}"
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{file_hash[:8]}_{suffix}"
        suffix += 1
    return candidate


def make_article_id(
    document_id: str,
    article_number: str,
    position: int,
    scope: str = "",
) -> str:
    """Identifiant d'article : ``<document_id>[_<portée>]_article_<numero>``.

    Args:
        scope: subdivision englobante lorsqu'elle ouvre une **numérotation
            propre** — typiquement une annexe. Un accord annexé recommence à
            « Article premier » : sans portée, son identifiant entrerait en
            collision avec celui du corps du texte, et le contrôle qualité
            signalerait un doublon là où il n'y a que deux séries distinctes.

    ``position`` sert de dernier recours lorsque le numéro est inexploitable.
    """
    number_slug = slugify(article_number, max_length=24) or str(position)
    prefix = f"{document_id}_{slugify(scope, max_length=24)}" if scope else document_id
    return f"{prefix}_article_{number_slug}"


# ---------------------------------------------------------------------------
# Numérotation juridique
# ---------------------------------------------------------------------------


def roman_to_int(value: str) -> int | None:
    """Convertit un nombre romain (``IV``, ``XII``) en entier, sinon ``None``."""
    text = value.strip().upper()
    if not text or not _ROMAN_RE.match(text):
        return None
    total = 0
    previous = 0
    for char in reversed(text):
        current = _ROMAN_VALUES[char]
        total = total - current if current < previous else total + current
        previous = max(previous, current)
    return total or None


def is_roman(value: str) -> bool:
    """Vrai si la chaîne est un nombre romain plausible."""
    return roman_to_int(value) is not None


def parse_number(value: str) -> float | None:
    """Convertit un numéro juridique en valeur triable.

    Gère les formes arabes (``45``), romaines (``XII``), littérales
    (``premier``) et les suffixes (``45 bis`` -> ``45.1``). Renvoie ``None``
    quand la valeur n'est pas interprétable — le pipeline ne devine pas.
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace("°", "")
    if not text:
        return None

    # Suffixe latin éventuel (bis, ter, quater...)
    suffix_value = 0.0
    match = re.search(
        r"\b(bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies)\b", text
    )
    if match:
        suffix_value = _SUFFIX_ORDER[match.group(1)] / 10.0
        text = text[: match.start()].strip()

    text = text.strip(" .-–—")

    if text in _WORD_ORDINALS:
        return _WORD_ORDINALS[text] + suffix_value

    # « 1er », « 2ème », « 45 »
    digits = re.match(r"^(\d+)\s*(?:er|ere|ère|e|eme|ème|°)?$", text)
    if digits:
        return int(digits.group(1)) + suffix_value

    # Numéros composés « 45-2 » : la partie principale porte le tri.
    compound = re.match(r"^(\d+)[-.](\d+)$", text)
    if compound:
        return int(compound.group(1)) + int(compound.group(2)) / 100.0 + suffix_value

    roman = roman_to_int(text)
    if roman is not None:
        return roman + suffix_value

    return None


# ---------------------------------------------------------------------------
# Entrées / sorties
# ---------------------------------------------------------------------------


def write_json(path: str | Path, data: Any, pretty: bool = True) -> Path:
    """Écrit un JSON UTF-8 (crée les dossiers parents si besoin)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2 if pretty else None)
        handle.write("\n")
    return target


def read_json(path: str | Path) -> Any:
    """Lit un fichier JSON UTF-8."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_jsonl(path: str | Path, records: Iterable[dict]) -> int:
    """Écrit un fichier JSON Lines et renvoie le nombre d'enregistrements."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> Iterator[dict]:
    """Itère sur les enregistrements d'un fichier JSON Lines."""
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def human_size(num_bytes: int) -> str:
    """Formatte une taille en octets de façon lisible."""
    size = float(num_bytes)
    for unit in ("o", "Kio", "Mio", "Gio"):
        if size < 1024 or unit == "Gio":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Gio"


def safe_relpath(path: str | Path, base: str | Path) -> str:
    """Chemin relatif à ``base`` si possible, sinon chemin absolu POSIX."""
    p, b = Path(path).resolve(), Path(base).resolve()
    try:
        return p.relative_to(b).as_posix()
    except ValueError:
        return p.as_posix()
