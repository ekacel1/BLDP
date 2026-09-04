"""Tests du module OCR (§7 module 4).

Les binaires OCR ne sont pas installés partout : les tests qui les exigent
vraiment portent le marqueur ``requires_ocr`` et sont ignorés automatiquement.
Tout le reste — construction des commandes, lecture de la confiance, repli en
cas d'indisponibilité — est vérifié en simulant les appels système, ce qui rend
la suite exécutable sur n'importe quelle machine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bldp.core.extraction import ocr as ocr_module
from bldp.core.extraction.ocr import (
    OcrError,
    OcrUnavailableError,
    _tesseract_confidence,
    available_engines,
    check_ocr_ready,
    extract_with_route,
    ocr_document,
    run_ocrmypdf,
)
from bldp.models import ExtractionMethod


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def no_ocr(monkeypatch):
    """Simule une machine sans aucun moteur OCR."""
    monkeypatch.setattr(ocr_module.shutil, "which", lambda name: None)


@pytest.fixture
def fake_ocrmypdf(monkeypatch):
    """Simule ocrmypdf : enregistre la commande et fabrique le PDF de sortie."""
    calls: list[list[str]] = []

    monkeypatch.setattr(
        ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "ocrmypdf" else None
    )

    def fake_run(command, **kwargs):
        calls.append(list(command))
        source, target = Path(command[-2]), Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())  # PDF « OCRisé » = copie du natif
        return FakeCompleted(0)

    monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)
    return calls


class TestEngineDetection:
    def test_no_engine_detected(self, no_ocr):
        assert available_engines() == []

    def test_preference_order(self, monkeypatch):
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(ocr_module, "tesseract_languages", lambda: ["fra", "eng"])
        assert available_engines() == ["ocrmypdf", "tesseract"]

    def test_check_reports_missing_engines(self, config, no_ocr):
        ready, problems = check_ocr_ready(config)
        assert ready is False
        assert any("aucun moteur OCR" in p for p in problems)

    def test_check_reports_disabled_ocr(self, config):
        cfg = config.with_overrides({"ocr": {"enabled": False}})
        ready, problems = check_ocr_ready(cfg)
        assert ready is False
        assert "désactivé" in problems[0]

    def test_check_reports_missing_language(self, config, monkeypatch):
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(ocr_module, "tesseract_languages", lambda: ["eng"])
        ready, problems = check_ocr_ready(config)
        assert ready is False
        assert any("fra" in p for p in problems)

    def test_check_passes_when_everything_present(self, config, monkeypatch):
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(ocr_module, "tesseract_languages", lambda: ["fra"])
        ready, problems = check_ocr_ready(config)
        assert ready is True and problems == []


class TestOcrmypdfCommand:
    def test_command_uses_configured_language_and_dpi(self, fake_ocrmypdf, text_pdf, tmp_path):
        run_ocrmypdf(text_pdf, tmp_path / "out.pdf", language="fra+eng", dpi=400)
        command = fake_ocrmypdf[0]
        assert command[0] == "ocrmypdf"
        assert "--language" in command and command[command.index("--language") + 1] == "fra+eng"
        assert command[command.index("--image-dpi") + 1] == "400"

    def test_skip_text_by_default(self, fake_ocrmypdf, text_pdf, tmp_path):
        run_ocrmypdf(text_pdf, tmp_path / "out.pdf")
        assert "--skip-text" in fake_ocrmypdf[0]

    def test_force_and_skip_are_mutually_exclusive(self, fake_ocrmypdf, text_pdf, tmp_path):
        run_ocrmypdf(text_pdf, tmp_path / "out.pdf", force=True)
        command = fake_ocrmypdf[0]
        assert "--force-ocr" in command and "--skip-text" not in command

    def test_original_is_never_modified(self, fake_ocrmypdf, text_pdf, tmp_path):
        before = text_pdf.read_bytes()
        run_ocrmypdf(text_pdf, tmp_path / "out.pdf")
        assert text_pdf.read_bytes() == before

    def test_missing_binary_raises_actionable_error(self, no_ocr, text_pdf, tmp_path):
        with pytest.raises(OcrUnavailableError, match="introuvable"):
            run_ocrmypdf(text_pdf, tmp_path / "out.pdf")

    def test_failure_is_reported_with_detail(self, monkeypatch, text_pdf, tmp_path):
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/usr/bin/ocrmypdf")
        monkeypatch.setattr(
            ocr_module.subprocess,
            "run",
            lambda *a, **k: FakeCompleted(2, stderr="EncryptedPdfError: chiffré"),
        )
        with pytest.raises(OcrError, match="chiffré"):
            run_ocrmypdf(text_pdf, tmp_path / "out.pdf")

    def test_timeout_is_reported(self, monkeypatch, text_pdf, tmp_path):
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: "/usr/bin/ocrmypdf")

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ocrmypdf", timeout=5)

        monkeypatch.setattr(ocr_module.subprocess, "run", raise_timeout)
        with pytest.raises(OcrError, match="interrompu"):
            run_ocrmypdf(text_pdf, tmp_path / "out.pdf", timeout=5)


class TestOcrDocument:
    def test_produces_text_and_keeps_auditable_pdf(self, fake_ocrmypdf, text_pdf, config):
        result = ocr_document(text_pdf, "loi", config)
        assert result.method is ExtractionMethod.OCR
        assert all(p.method is ExtractionMethod.OCR for p in result.pages)
        assert result.total_chars > 0
        assert result.ocr_pdf_path and Path(result.ocr_pdf_path).exists()

    def test_sidecar_pdf_can_be_disabled(self, fake_ocrmypdf, text_pdf, config):
        cfg = config.with_overrides({"ocr": {"keep_sidecar_pdf": False}})
        result = ocr_document(text_pdf, "loi", cfg)
        assert result.ocr_pdf_path is None
        assert result.total_chars > 0

    def test_no_engine_raises(self, no_ocr, text_pdf, config):
        with pytest.raises(OcrUnavailableError, match="Aucun moteur OCR"):
            ocr_document(text_pdf, "loi", config)

    def test_targeted_ocr_requires_tesseract(self, fake_ocrmypdf, text_pdf, config):
        """ocrmypdf ne sait pas traiter un sous-ensemble de pages."""
        with pytest.raises(OcrUnavailableError, match="Tesseract"):
            ocr_document(text_pdf, "loi", config, pages=[2])


class TestRouting:
    def test_native_route_uses_pymupdf(self, text_pdf, config):
        result = extract_with_route(text_pdf, "loi", "native", config)
        assert result.method is ExtractionMethod.NATIVE
        assert "Article 1er" in result.pages[0].text

    def test_ocr_route_falls_back_and_flags_it(self, no_ocr, text_pdf, config):
        """§26 : l'indisponibilité de l'OCR ne fait pas échouer le document."""
        result = extract_with_route(text_pdf, "loi", "ocr", config)
        assert result.method is ExtractionMethod.NATIVE
        assert result.pages, "le texte natif disponible est conservé"
        assert any("indisponible" in w for w in result.warnings)
        assert any("ocr_indisponible" in e for e in result.errors)

    def test_hybrid_without_ocr_keeps_native_and_warns(self, no_ocr, text_pdf, config):
        result = extract_with_route(text_pdf, "loi", "hybrid", config, ocr_pages=[2])
        assert result.method is ExtractionMethod.NATIVE
        assert any("n'ont pas pu être OCRisées" in w for w in result.warnings)

    def test_hybrid_replaces_only_when_ocr_adds_text(self, monkeypatch, text_pdf, config):
        """En cas de doute, le texte natif est conservé (§9)."""
        from bldp.models import ExtractionResult, Page

        def fake_ocr(path, document_id, cfg, output_dir=None, pages=None, source_file=None):
            return ExtractionResult(
                document_id=document_id,
                source_file="loi.pdf",
                method=ExtractionMethod.OCR,
                pages=[
                    Page(
                        document_id=document_id,
                        page=int(pages[0]),
                        text="x",  # plus pauvre que le natif
                        source_file="loi.pdf",
                        method=ExtractionMethod.OCR,
                    )
                ],
            )

        monkeypatch.setattr(ocr_module, "ocr_document", fake_ocr)
        result = extract_with_route(text_pdf, "loi", "hybrid", config, ocr_pages=[2])
        assert "Article 3" in result.pages[1].text, "le natif, plus riche, est conservé"
        assert any("0/1 page(s) améliorée" in w for w in result.warnings)

    def test_hybrid_replaces_when_ocr_is_richer(self, monkeypatch, empty_pdf, config):
        from bldp.models import ExtractionResult, Page

        def fake_ocr(path, document_id, cfg, output_dir=None, pages=None, source_file=None):
            return ExtractionResult(
                document_id=document_id,
                source_file="vide.pdf",
                method=ExtractionMethod.OCR,
                pages=[
                    Page(
                        document_id=document_id,
                        page=int(pages[0]),
                        text="Article 1er : texte retrouve par OCR.",
                        source_file="vide.pdf",
                        method=ExtractionMethod.OCR,
                        ocr_confidence=0.91,
                    )
                ],
            )

        monkeypatch.setattr(ocr_module, "ocr_document", fake_ocr)
        result = extract_with_route(empty_pdf, "vide", "hybrid", config, ocr_pages=[2])
        assert result.method is ExtractionMethod.MIXED
        assert "retrouve par OCR" in result.pages[1].text
        assert "page_ocrisee_en_remplacement_du_natif" in result.pages[1].warnings

    def test_unknown_route_is_rejected(self, text_pdf, config):
        with pytest.raises(ValueError, match="Itinéraire"):
            extract_with_route(text_pdf, "loi", "magique", config)


class TestConfidenceParsing:
    def test_reads_word_confidences_from_tsv(self, monkeypatch, tmp_path):
        header = "level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext"
        rows = [
            header,
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95.0\tArticle",
            "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t85.0\t45",
            "4\t1\t1\t1\t1\t0\t0\t0\t10\t10\t-1\t",  # ligne de structure ignorée
        ]
        monkeypatch.setattr(
            ocr_module.subprocess, "run", lambda *a, **k: FakeCompleted(0, "\n".join(rows))
        )
        assert _tesseract_confidence(tmp_path / "p.png", "fra", 30) == pytest.approx(0.90)

    def test_returns_none_when_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            ocr_module.subprocess, "run", lambda *a, **k: FakeCompleted(1, "")
        )
        assert _tesseract_confidence(tmp_path / "p.png", "fra", 30) is None


@pytest.mark.requires_ocr
class TestRealOcr:
    """Tests de bout en bout, exécutés seulement si les binaires sont présents."""

    def test_scanned_pdf_yields_readable_text(self, scanned_pdf, config):
        ready, problems = check_ocr_ready(config)
        if not ready:
            pytest.skip("OCR non opérationnel : " + " ; ".join(problems))
        result = ocr_document(scanned_pdf, "scan", config)
        assert result.total_chars > 100
        assert "Article" in result.full_text


class TestOutputEncoding:
    """Régression : la sortie OCR était décodée avec l'encodage local.

    Tesseract et OCRmyPDF écrivent en UTF-8. Sans `encoding="utf-8"`,
    `subprocess` décodait en cp1252 sur Windows et tout le corpus ressortait
    en mojibake — « présente » devenait « prÃ©sente ». Sur un corpus
    juridique français, cela corrompt chaque mot accentué.
    """

    def test_accented_output_is_read_as_utf8(self, monkeypatch, tmp_path):
        texte = "La présente loi sera exécutée comme Loi de l'État."

        def fake_run(command, **kwargs):
            assert kwargs.get("encoding") == "utf-8", (
                "la sortie OCR doit être décodée explicitement en UTF-8"
            )
            return FakeCompleted(0, stdout=texte)

        monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)
        assert ocr_module._tesseract_text(tmp_path / "p.png", "fra", 30) == texte

    def test_every_subprocess_call_declares_utf8(self):
        """Aucun appel de sous-processus ne doit retomber sur l'encodage machine.

        La vérification porte sur les appels eux-mêmes, et non sur un décompte
        d'occurrences dans le fichier : compter ``text=True`` et
        ``encoding="utf-8"`` donnait le bon résultat par coïncidence, et se
        déséquilibrait dès qu'une lecture de fichier déclarait son encodage
        sans être un sous-processus.
        """
        import ast
        import inspect

        arbre = ast.parse(inspect.getsource(ocr_module))
        fautifs = []
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call):
                continue
            cible = ast.unparse(noeud.func)
            if not cible.endswith("subprocess.run"):
                continue
            mots = {k.arg: k.value for k in noeud.keywords}
            if "text" not in mots:
                continue
            encodage = mots.get("encoding")
            if not (isinstance(encodage, ast.Constant) and encodage.value == "utf-8"):
                fautifs.append(noeud.lineno)

        assert not fautifs, (
            "appel(s) de subprocess.run en mode texte sans encodage explicite, "
            f"ligne(s) {fautifs}"
        )


class TestReadinessIsHonest:
    """`check_ocr_ready` ne doit jamais annoncer un OCR qui échouera.

    Régression trouvée en usage réel : avec le paquet pip `ocrmypdf` installé
    mais **sans** le binaire Tesseract, le diagnostic répondait « opérationnel :
    OUI » — puis chaque document échouait. OCRmyPDF n'est qu'un pilote.
    """

    def test_ocrmypdf_without_tesseract_is_not_ready(self, config, monkeypatch):
        monkeypatch.setattr(
            ocr_module.shutil,
            "which",
            lambda name: "/usr/bin/ocrmypdf" if name == "ocrmypdf" else None,
        )
        ready, problems = check_ocr_ready(config)
        assert ready is False
        assert any("tesseract est introuvable" in p for p in problems)

    def test_unlistable_languages_is_not_ready(self, config, monkeypatch):
        """TESSDATA_PREFIX mal configuré : on le dit, on ne le devine pas."""
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(ocr_module, "tesseract_languages", lambda: [])
        ready, problems = check_ocr_ready(config)
        assert ready is False
        assert any("TESSDATA_PREFIX" in p for p in problems)

    def test_ready_only_when_everything_is_in_place(self, config, monkeypatch):
        monkeypatch.setattr(ocr_module.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(ocr_module, "tesseract_languages", lambda: ["eng", "fra", "osd"])
        assert check_ocr_ready(config) == (True, [])


class TestFailureIsDiagnosable:
    """« erreur inconnue » n'aide personne à réparer quoi que ce soit.

    Sur un lot de 46 documents traités à quatre fils, Tesseract est sorti en
    erreur sans rien écrire : le corpus a bien été produit (§26), mais le
    message ne permettait pas de comprendre pourquoi.
    """

    def test_stderr_is_reported_when_present(self):
        from bldp.core.extraction.ocr import _tesseract_failure

        completed = FakeCompleted(1, stderr="Error opening data file fra.traineddata")
        assert "fra.traineddata" in _tesseract_failure(completed)

    def test_stdout_is_used_when_stderr_is_silent(self):
        """Tesseract écrit parfois son erreur sur la sortie standard."""
        from bldp.core.extraction.ocr import _tesseract_failure

        completed = FakeCompleted(1, stdout="Image too large")
        assert "Image too large" in _tesseract_failure(completed)

    @pytest.mark.parametrize("code", [-9, 137])
    def test_a_process_killed_by_the_system_is_named_as_such(self, code):
        """Le cas rencontré : aucun message, seulement un code de retour."""
        from bldp.core.extraction.ocr import _tesseract_failure

        message = _tesseract_failure(FakeCompleted(code))
        assert "mémoire" in message
        assert "--workers" in message, "le message doit dire quoi faire"

    def test_a_silent_failure_still_says_what_to_try(self):
        from bldp.core.extraction.ocr import _tesseract_failure

        message = _tesseract_failure(FakeCompleted(1))
        assert "TESSDATA_PREFIX" in message and "code de retour 1" in message


class TestUneSeuleReconnaissanceParPage:
    """L'OCR est l'étape la plus chère : la faire deux fois se paie cher.

    BLDP lançait Tesseract deux fois par page — une pour le texte, une pour la
    sortie TSV dont il tirait la confiance. Mesuré sur des décrets scannés du
    corpus SGG : **11,89 s par page en deux appels, 5,85 s en un seul**, pour
    un texte identique au caractère près.
    """

    def test_le_texte_et_la_confiance_viennent_du_meme_appel(self, monkeypatch, tmp_path):
        appels = []

        def faux_run(command, **kwargs):
            appels.append(command)
            base = Path(command[2])
            base.with_suffix(".txt").write_text(
                "Article premier : la présente loi.", encoding="utf-8"
            )
            base.with_suffix(".tsv").write_text(
                "level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext\n"
                "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t92.5\tArticle\n"
                "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t87.5\tpremier\n",
                encoding="utf-8",
            )
            return FakeCompleted(0, stdout="")

        monkeypatch.setattr(ocr_module.subprocess, "run", faux_run)
        image = tmp_path / "page_1.png"
        image.write_bytes(b"\x89PNG")

        texte, confiance = ocr_module._tesseract_page(image, "fra", 30)

        assert len(appels) == 1, "une page ne doit demander qu'une reconnaissance"
        assert "txt" in appels[0] and "tsv" in appels[0]
        assert texte.startswith("Article premier")
        assert confiance == 0.9

    def test_une_confiance_illisible_ne_perd_pas_le_texte(self, monkeypatch, tmp_path):
        """La confiance est un supplément ; le texte est le produit."""

        def faux_run(command, **kwargs):
            Path(command[2]).with_suffix(".txt").write_text(
                "Texte récupéré.", encoding="utf-8"
            )
            return FakeCompleted(0, stdout="")

        monkeypatch.setattr(ocr_module.subprocess, "run", faux_run)
        image = tmp_path / "page_1.png"
        image.write_bytes(b"\x89PNG")

        texte, confiance = ocr_module._tesseract_page(image, "fra", 30)
        assert texte == "Texte récupéré."
        assert confiance is None

    def test_un_echec_de_tesseract_est_signale(self, monkeypatch, tmp_path):
        def faux_run(command, **kwargs):
            return FakeCompleted(1, stderr="Error in pixReadStream")

        monkeypatch.setattr(ocr_module.subprocess, "run", faux_run)
        image = tmp_path / "page_1.png"
        image.write_bytes(b"\x89PNG")

        with pytest.raises(ocr_module.OcrError) as erreur:
            ocr_module._tesseract_page(image, "fra", 30)
        assert "pixReadStream" in str(erreur.value)

    def test_les_lignes_sans_mot_ne_faussent_pas_la_confiance(self):
        """Tesseract note -1 les lignes de structure : elles ne comptent pas."""
        tsv = (
            "level\tpage\tblock\tpar\tline\tword\tleft\ttop\tw\th\tconf\ttext\n"
            "1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t-1\t\n"
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t80.0\tArticle\n"
            "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t60.0\tpremier\n"
        )
        assert ocr_module._confiance_depuis_tsv(tsv) == 0.7
