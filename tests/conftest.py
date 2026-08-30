"""Fixtures partagées : génération de PDF de test.

Les documents d'essai sont fabriqués à la volée avec PyMuPDF plutôt que
versionnés en binaire : les tests restent lisibles, reproductibles et le dépôt
ne contient aucun document juridique réel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bldp.config import load_config

# --------------------------------------------------------------------------
# Textes juridiques de démonstration (fictifs)
# --------------------------------------------------------------------------

LEGAL_TEXT_PAGES: list[str] = [
    (
        "REPUBLIQUE DU BENIN\n"
        "LOI N° 2026-001 DU 10 FEVRIER 2026\n"
        "portant organisation du travail en Republique du Benin\n"
        "\n"
        "TITRE PREMIER\n"
        "DISPOSITIONS GENERALES\n"
        "\n"
        "CHAPITRE I\n"
        "DE L'OBJET ET DU CHAMP D'APPLICATION\n"
        "\n"
        "Article 1er : La presente loi fixe les regles applicables aux relations\n"
        "de travail entre les employeurs et les travailleurs.\n"
        "\n"
        "Article 2 : Est considere comme travailleur toute personne physique qui\n"
        "s'engage a mettre son activite professionnelle sous la direction d'une\n"
        "autre personne moyennant remuneration.\n"
    ),
    (
        "CHAPITRE II\n"
        "DU CONTRAT DE TRAVAIL\n"
        "\n"
        "Section 1\n"
        "De la formation du contrat\n"
        "\n"
        "Article 3 : Le contrat de travail est conclu librement.\n"
        "Il peut etre a duree determinee ou indeterminee.\n"
        "\n"
        "Article 4 : Le contrat a duree determinee ne peut exceder quatre ans,\n"
        "renouvellement compris.\n"
        "\n"
        "Article 5 : Toute clause contraire aux dispositions de la presente loi\n"
        "est reputee non ecrite.\n"
    ),
]


def _write_text_pdf(path: Path, pages: list[str], fontsize: int = 11) -> Path:
    """Crée un PDF natif (couche texte réelle) à partir de pages de texte.

    ``insert_textbox`` renvoie une valeur négative lorsque le texte déborde du
    cadre : il n'écrit alors **rien**. On lève dans ce cas, sans quoi un test
    pourrait passer sur un PDF silencieusement vide.
    """
    pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF requis pour les tests PDF")
    document = pymupdf.open()
    for index, content in enumerate(pages):
        page = document.new_page(width=595, height=842)  # A4
        rect = pymupdf.Rect(50, 50, 545, 792)
        remaining = page.insert_textbox(rect, content, fontsize=fontsize, fontname="helv")
        if content.strip() and remaining < 0:
            raise ValueError(
                f"Texte trop long pour la page {index + 1} de {path.name} : "
                "réduisez le contenu de la fixture."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()
    return path


def _write_image_pdf(path: Path, pages: list[str], dpi: int = 120) -> Path:
    """Crée un PDF « scanné » : chaque page est une image, sans couche texte."""
    pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF requis pour les tests PDF")
    source = pymupdf.open()
    for content in pages:
        page = source.new_page(width=595, height=842)
        page.insert_textbox(
            pymupdf.Rect(50, 50, 545, 792), content, fontsize=11, fontname="helv"
        )

    scanned = pymupdf.open()
    for index in range(source.page_count):
        pixmap = source.load_page(index).get_pixmap(dpi=dpi)
        page = scanned.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pixmap)
    path.parent.mkdir(parents=True, exist_ok=True)
    scanned.save(path)
    scanned.close()
    source.close()
    return path


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_logging():
    """Restaure le logger BLDP après chaque test.

    ``setup_logging`` (appelé par la CLI) fixe ``propagate = False`` et installe
    ses propres gestionnaires. Sans ce nettoyage, un test invoquant la CLI rend
    ``caplog`` aveugle pour tous les tests suivants — l'état fuit d'un test à
    l'autre et les échecs deviennent dépendants de l'ordre d'exécution.
    """
    import logging

    logger = logging.getLogger("bldp")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    try:
        yield
    finally:
        for handler in list(logger.handlers):
            if handler not in saved_handlers:
                logger.removeHandler(handler)
                handler.close()
        for handler in saved_handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


@pytest.fixture
def config(tmp_path):
    """Configuration standard, dont tous les chemins pointent dans ``tmp_path``."""
    cfg = load_config(root=tmp_path)
    cfg.ensure_directories()
    return cfg


@pytest.fixture
def legal_pages() -> list[str]:
    return list(LEGAL_TEXT_PAGES)


@pytest.fixture
def text_pdf(tmp_path) -> Path:
    """PDF natif contenant une loi fictive sur deux pages."""
    return _write_text_pdf(tmp_path / "loi_2026_001.pdf", LEGAL_TEXT_PAGES)


@pytest.fixture
def scanned_pdf(tmp_path) -> Path:
    """PDF sans couche texte, imitant un document numérisé."""
    return _write_image_pdf(tmp_path / "loi_scannee.pdf", LEGAL_TEXT_PAGES)


@pytest.fixture
def empty_pdf(tmp_path) -> Path:
    """PDF de trois pages entièrement vides."""
    return _write_text_pdf(tmp_path / "vide.pdf", ["", "", ""])


@pytest.fixture
def headers_pdf(tmp_path) -> Path:
    """PDF de quatre pages avec en-tête, pied de page et numéro répétés."""
    pages = []
    body = [
        "Article 1er : Premiere disposition du texte.",
        "Article 2 : Deuxieme disposition du texte.",
        "Article 3 : Troisieme disposition du texte.",
        "Article 4 : Quatrieme disposition du texte.",
    ]
    for index, line in enumerate(body, start=1):
        pages.append(
            "\n".join(
                [
                    "JOURNAL OFFICIEL DE LA REPUBLIQUE DU BENIN",
                    "",
                    line,
                    "Cette disposition entre en vigueur des sa publication.",
                    "",
                    "Secretariat General du Gouvernement",
                    str(index),
                ]
            )
        )
    return _write_text_pdf(tmp_path / "journal_officiel.pdf", pages)


@pytest.fixture
def make_text_pdf(tmp_path):
    """Fabrique paramétrable : ``make_text_pdf("nom.pdf", ["page 1", ...])``."""

    def _factory(name: str, pages: list[str]) -> Path:
        return _write_text_pdf(tmp_path / name, pages)

    return _factory
