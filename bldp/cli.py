"""Interface en ligne de commande de BLDP (§21 du cahier des charges).

Le parseur d'arguments est défini une fois pour toutes ici ; chaque commande est
déléguée à un gestionnaire dédié. Les gestionnaires importent leurs dépendances
**paresseusement** afin qu'une commande n'exige pas l'installation des extras
qu'elle n'utilise pas (OCR, embeddings, FAISS, web).

Commandes prévues ::

    python -m bldp ingest   ./input
    python -m bldp process  ./input
    python -m bldp validate ./data/processed
    python -m bldp embed    ./data/validated
    python -m bldp export
    python -m bldp pipeline ./input
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from bldp import __version__
from bldp.config import Config, ConfigError, load_config
from bldp.logging_setup import get_logger, setup_logging

#: Code de sortie renvoyé lorsqu'une commande n'est pas encore disponible.
EXIT_NOT_IMPLEMENTED = 3
EXIT_ERROR = 1
EXIT_OK = 0


# ---------------------------------------------------------------------------
# Construction du parseur
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bldp",
        description=(
            "Benin Legal Data Pipeline — transforme des documents juridiques "
            "bruts en corpus structuré et auditable."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  python -m bldp ingest ./input\n"
            "  python -m bldp process ./input --limit 5\n"
            "  python -m bldp pipeline ./input\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"bldp {__version__}")

    # Options globales, disponibles avant comme après la sous-commande.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", metavar="FICHIER", help="fichier YAML de configuration additionnel"
    )
    common.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="CLE=VALEUR",
        help="surcharge ponctuelle, ex. --set ocr.enabled=false (répétable)",
    )
    common.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="niveau de journalisation (défaut : celui de la configuration)",
    )
    common.add_argument(
        "--root",
        metavar="DOSSIER",
        help="racine de travail où résoudre data/, logs/ et input/ "
        "(défaut : la racine du dépôt)",
    )
    common.add_argument(
        "-q", "--quiet", action="store_true", help="n'afficher que les erreurs"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMANDE")

    # -- config -------------------------------------------------------------
    p_config = subparsers.add_parser(
        "config",
        parents=[common],
        help="afficher la configuration effective",
        description="Affiche la configuration après fusion des fichiers et surcharges.",
    )
    p_config.add_argument(
        "key", nargs="?", help="clé pointée à afficher (ex. ocr.language)"
    )
    p_config.set_defaults(func=cmd_config)

    # -- doctor -------------------------------------------------------------
    p_doctor = subparsers.add_parser(
        "doctor",
        parents=[common],
        help="vérifier l'environnement (dépendances, binaires OCR)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    # -- ingest -------------------------------------------------------------
    p_ingest = subparsers.add_parser(
        "ingest",
        parents=[common],
        help="inventorier les documents d'un dossier d'entrée",
        description=(
            "Parcourt un dossier, calcule l'empreinte de chaque document, lui "
            "attribue un identifiant stable et en copie l'original dans data/raw/. "
            "Les fichiers d'origine ne sont jamais modifiés."
        ),
    )
    p_ingest.add_argument(
        "input", nargs="?", help="dossier ou fichier à importer (défaut : paths.input)"
    )
    p_ingest.add_argument(
        "--no-copy", action="store_true", help="ne pas copier les originaux dans data/raw/"
    )
    p_ingest.add_argument(
        "--json", action="store_true", help="afficher l'inventaire au format JSON"
    )
    p_ingest.set_defaults(func=cmd_ingest)

    # -- analyze ------------------------------------------------------------
    p_analyze = subparsers.add_parser(
        "analyze",
        parents=[common],
        help="analyser des PDF et dire lesquels nécessitent un OCR",
        description=(
            "Mesure chaque PDF (pages, texte, images) et décide si un OCR est "
            "nécessaire. La décision est toujours motivée et accompagnée d'un "
            "score de confiance."
        ),
    )
    p_analyze.add_argument(
        "input", nargs="?", help="dossier ou PDF à analyser (défaut : paths.input)"
    )
    p_analyze.add_argument(
        "--json", action="store_true", help="sortie JSON complète (détail par page)"
    )
    p_analyze.set_defaults(func=cmd_analyze)

    # -- extract ------------------------------------------------------------
    p_extract = subparsers.add_parser(
        "extract",
        parents=[common],
        help="extraire le texte d'un PDF (diagnostic)",
        description=(
            "Extrait le texte page par page d'un seul PDF et l'affiche ou "
            "l'enregistre. Commande de diagnostic : le traitement complet passe "
            "par `process`."
        ),
    )
    p_extract.add_argument("pdf", help="chemin du PDF à lire")
    p_extract.add_argument(
        "--pages", help="pages à extraire, ex. 1,3,5-8 (défaut : toutes)"
    )
    p_extract.add_argument("-o", "--output", help="fichier de sortie (.txt ou .json)")
    p_extract.add_argument(
        "--clean",
        action="store_true",
        help="appliquer le nettoyage (module 5) et afficher le rapport",
    )
    p_extract.set_defaults(func=cmd_extract)

    # -- parse --------------------------------------------------------------
    p_parse = subparsers.add_parser(
        "parse",
        parents=[common],
        help="extraire, nettoyer et découper un PDF en articles (diagnostic)",
        description=(
            "Enchaîne extraction, nettoyage et parsing juridique sur un seul "
            "PDF, puis affiche la structure et les articles détectés."
        ),
    )
    p_parse.add_argument("pdf", help="chemin du PDF à analyser")
    p_parse.add_argument(
        "--full", action="store_true", help="afficher le texte intégral des articles"
    )
    p_parse.add_argument("-o", "--output", help="écrire le résultat en JSON")
    p_parse.set_defaults(func=cmd_parse)

    # -- pipeline -----------------------------------------------------------
    p_pipeline = subparsers.add_parser(
        "pipeline",
        parents=[common],
        help="exécuter le pipeline complet, de l'import aux exports",
        description=(
            "Enchaîne import, analyse, extraction, OCR si nécessaire, nettoyage, "
            "parsing, métadonnées, relations, doublons, contrôle qualité, puis "
            "produit la base SQLite et les exports JSONL. Une erreur sur un "
            "document n'interrompt pas les autres."
        ),
    )
    p_pipeline.add_argument(
        "input", nargs="?", help="dossier ou PDF à traiter (défaut : paths.input)"
    )
    p_pipeline.add_argument(
        "--limit", type=int, help="ne traiter que les N premiers documents"
    )
    p_pipeline.add_argument(
        "--embed",
        action="store_true",
        help="générer aussi les embeddings et l'index vectoriel",
    )
    p_pipeline.add_argument(
        "--no-embed", action="store_true", help="ne jamais générer d'embeddings"
    )
    p_pipeline.add_argument(
        "--no-export", action="store_true", help="ne pas produire les exports"
    )
    p_pipeline.set_defaults(func=cmd_pipeline)

    # -- process ------------------------------------------------------------
    p_process = subparsers.add_parser(
        "process",
        parents=[common],
        help="traiter des documents sans produire les exports",
        description=(
            "Traite les documents et écrit un JSON par document dans "
            "data/processed/, prêt pour une revue avant export."
        ),
    )
    p_process.add_argument("input", nargs="?", help="dossier ou PDF à traiter")
    p_process.add_argument("--limit", type=int, help="ne traiter que les N premiers documents")
    p_process.set_defaults(func=cmd_process)

    # -- validate -----------------------------------------------------------
    p_validate = subparsers.add_parser(
        "validate",
        parents=[common],
        help="passer en revue la qualité et enregistrer une décision humaine",
        description=(
            "Sans argument, liste les documents à vérifier par ordre de gravité. "
            "Avec --document, affiche la comparaison page d'origine / texte "
            "nettoyé / article structuré, et permet d'enregistrer une décision."
        ),
    )
    p_validate.add_argument(
        "path", nargs="?", help="dossier data/processed/ à relire (facultatif)"
    )
    p_validate.add_argument("--document", help="identifiant du document à examiner")
    p_validate.add_argument("--article", help="n'afficher qu'un article")
    p_validate.add_argument(
        "--set-status",
        choices=["valide", "a_verifier", "rejete", "en_attente"],
        help="enregistrer la décision de validation pour --document",
    )
    p_validate.add_argument("--note", default="", help="commentaire accompagnant la décision")
    p_validate.set_defaults(func=cmd_validate)

    # -- embed --------------------------------------------------------------
    p_embed = subparsers.add_parser(
        "embed",
        parents=[common],
        help="générer les embeddings et l'index vectoriel (optionnel)",
        description=(
            "Découpe le corpus en fragments juridiquement cohérents, génère les "
            "vecteurs avec Sentence Transformers et construit l'index FAISS. "
            "Tout se passe localement."
        ),
    )
    p_embed.add_argument("path", nargs="?", help="dossier de documents traités (facultatif)")
    p_embed.add_argument("--model", help="surcharge de embeddings.model")
    p_embed.add_argument(
        "--dry-run",
        action="store_true",
        help="produire seulement les fragments, sans générer de vecteurs",
    )
    p_embed.set_defaults(func=cmd_embed)

    # -- search -------------------------------------------------------------
    p_search = subparsers.add_parser(
        "search",
        parents=[common],
        help="rechercher dans le corpus (plein texte, ou vectoriel si indexé)",
    )
    p_search.add_argument("query", help="texte recherché")
    p_search.add_argument("-k", "--top", type=int, default=5, help="nombre de résultats")
    p_search.add_argument(
        "--vector", action="store_true", help="utiliser l'index vectoriel plutôt que le plein texte"
    )
    p_search.set_defaults(func=cmd_search)

    # -- export -------------------------------------------------------------
    p_export = subparsers.add_parser(
        "export",
        parents=[common],
        help="réexporter le corpus enregistré en base",
        description=(
            "Régénère documents.jsonl, articles.jsonl, metadata.json et "
            "quality_report.json à partir de la base SQLite."
        ),
    )
    p_export.add_argument("-o", "--output", help="dossier de sortie (défaut : paths.exports)")
    p_export.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=["jsonl", "json", "csv", "sqlite"],
        help="format à produire (répétable ; défaut : export.formats)",
    )
    p_export.set_defaults(func=cmd_export)

    # -- serve --------------------------------------------------------------
    p_serve = subparsers.add_parser(
        "serve",
        parents=[common],
        help="lancer l'interface web locale (optionnelle)",
        description=(
            "Démarre une interface web minimale permettant de déposer un PDF, "
            "suivre son traitement, comparer le texte extrait à l'original, "
            "valider manuellement et télécharger les exports. Le serveur "
            "n'écoute que sur la boucle locale par défaut."
        ),
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="adresse d'écoute")
    p_serve.add_argument("--port", type=int, default=8000, help="port d'écoute")
    p_serve.add_argument("--reload", action="store_true", help="rechargement automatique")
    p_serve.set_defaults(func=cmd_serve)

    # -- stats --------------------------------------------------------------
    p_stats = subparsers.add_parser(
        "stats", parents=[common], help="afficher l'état du corpus enregistré"
    )
    p_stats.set_defaults(func=cmd_stats)

    # -- trace --------------------------------------------------------------
    p_trace = subparsers.add_parser(
        "trace",
        parents=[common],
        help="remonter d'un article à sa page et à son fichier d'origine",
        description=(
            "Affiche la chaîne complète article → document → page → fichier "
            "source, afin de vérifier à la main la fidélité de l'extraction (§33)."
        ),
    )
    p_trace.add_argument("article_id", help="identifiant de l'article, ex. code_travail_article_45")
    p_trace.set_defaults(func=cmd_trace)

    return parser


# ---------------------------------------------------------------------------
# Gestionnaires
# ---------------------------------------------------------------------------


def cmd_config(args: argparse.Namespace, config: Config) -> int:
    import json

    if args.key:
        sentinel = object()
        value = config.get(args.key, sentinel)
        if value is sentinel:
            print(f"Clé inconnue : {args.key}", file=sys.stderr)
            return EXIT_ERROR
        print(json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else value)
        return EXIT_OK

    data = config.as_dict()
    data.pop("_root", None)
    print("# Sources : " + ", ".join(str(s) for s in config.sources))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    """Diagnostique l'environnement local : rien n'est installé en douce."""
    import importlib.util
    import shutil

    def check_module(name: str, purpose: str, required: bool) -> bool:
        found = importlib.util.find_spec(name) is not None
        mark = "OK " if found else ("MANQUANT" if required else "absent  ")
        print(f"  [{mark:8}] {name:24} {purpose}")
        return found

    def check_binary(name: str, purpose: str) -> bool:
        found = shutil.which(name) is not None
        print(f"  [{'OK ' if found else 'absent  ':8}] {name:24} {purpose}")
        return found

    print(f"BLDP {__version__} — diagnostic de l'environnement")
    print(f"  Python {sys.version.split()[0]} ({sys.platform})")
    print("\nDépendances du coeur (obligatoires) :")
    core_ok = all(
        [
            check_module("fitz", "extraction PDF native (PyMuPDF)", True),
            check_module("yaml", "lecture de la configuration", True),
        ]
    )

    print("\nOCR (optionnel — requis seulement pour les PDF scannés) :")
    check_module("ocrmypdf", "pilotage OCR sur PDF", False)
    check_module("pytesseract", "OCR page par page", False)
    check_binary("tesseract", "moteur OCR système")
    check_binary("ocrmypdf", "outil OCR système")

    from bldp.core.extraction.ocr import check_ocr_ready, tesseract_languages

    languages = tesseract_languages()
    if languages:
        print(f"    langues Tesseract installées : {', '.join(languages)}")
    ready, problems = check_ocr_ready(config)
    print(f"    OCR opérationnel : {'OUI' if ready else 'NON'}")
    for problem in problems:
        print(f"      -> {problem}")

    print("\nEmbeddings et base vectorielle (optionnels, désactivés par défaut) :")
    check_module("sentence_transformers", "génération d'embeddings", False)
    check_module("faiss", "index vectoriel FAISS", False)
    check_module("numpy", "calcul vectoriel", False)

    print("\nInterface web (optionnelle) :")
    check_module("fastapi", "serveur web", False)
    check_module("uvicorn", "serveur ASGI", False)

    print("\nRépertoires de travail :")
    for key in ("input", "raw", "processed", "validated", "embeddings", "exports"):
        path = config.path(key)
        print(f"  [{'OK ' if path.exists() else 'à créer':8}] {key:12} {path}")

    print(
        "\nAppels externes autorisés : "
        + ("OUI" if config.get("privacy.allow_external_calls") else "NON (traitement 100% local)")
    )
    return EXIT_OK if core_ok else EXIT_ERROR


def cmd_ingest(args: argparse.Namespace, config: Config) -> int:
    import json

    from bldp.core.loader import LoaderError, ingest
    from bldp.utils import human_size

    logger = get_logger()
    root = args.input or config.path("input")
    config.ensure_directories()

    try:
        sources = ingest(root, config, copy=not args.no_copy)
    except LoaderError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR

    if args.json:
        print(json.dumps([s.to_dict() for s in sources], ensure_ascii=False, indent=2))
    else:
        for source in sources:
            flag = "ERREUR" if source.error else "OK"
            print(
                f"  [{flag:6}] {source.document_id:40} "
                f"{human_size(source.size_bytes):>10}  {source.category}"
            )
            if source.error:
                print(f"           -> {source.error}")

    failed = sum(1 for s in sources if s.error)
    print(f"\n{len(sources)} document(s) inventorié(s), {failed} en erreur.")
    return EXIT_OK if not failed else EXIT_ERROR


def cmd_analyze(args: argparse.Namespace, config: Config) -> int:
    import json

    from bldp.core.classifier import analyze_or_none, decide_extraction_route
    from bldp.core.loader import LoaderError, discover_files
    from bldp.utils import slugify

    logger = get_logger()
    root = args.input or config.path("input")
    try:
        paths = discover_files(root, config.get("ingest.extensions", [".pdf"]))
    except LoaderError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR

    if not paths:
        print(f"Aucun document à analyser dans {root}")
        return EXIT_OK

    results = []
    for path in paths:
        analysis = analyze_or_none(path, slugify(path.stem), config)
        if analysis is None:
            print(f"  [ILLISIBLE] {path.name}")
            continue
        route = decide_extraction_route(analysis, config)
        results.append((path, analysis, route))

    if args.json:
        print(
            json.dumps(
                [
                    {**a.to_dict(), "route": route, "file": str(p)}
                    for p, a, route in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK

    print(f"{'DOCUMENT':40} {'PAGES':>6} {'TEXTE':>7} {'OCR':>10} {'CONF.':>7}  ROUTE")
    print("-" * 88)
    for path, analysis, route in results:
        print(
            f"{analysis.document_id[:40]:40} {analysis.pages:>6} "
            f"{'oui' if analysis.has_text else 'non':>7} "
            f"{'requis' if analysis.ocr_required else 'inutile':>10} "
            f"{analysis.confidence:>7.2f}  {route}"
        )
        for reason in analysis.reasons:
            print(f"    · {reason}")
    return EXIT_OK


def cmd_extract(args: argparse.Namespace, config: Config) -> int:
    import json
    from pathlib import Path

    from bldp.core.extraction.pymupdf_extractor import ExtractionError, extract_document
    from bldp.utils import slugify, write_json

    logger = get_logger()
    pdf_path = Path(args.pdf)
    try:
        result = extract_document(
            pdf_path,
            document_id=slugify(pdf_path.stem),
            pages=_parse_page_selection(args.pages),
        )
    except ExtractionError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR

    if args.clean:
        from bldp.core.cleaning.normalizer import clean_pages

        result.pages, report = clean_pages(result.pages, config, result.document_id)
        print("--- rapport de nettoyage ---")
        for key, value in report.to_dict().items():
            if value not in (0, [], "", None):
                print(f"  {key}: {value}")
        print()

    if args.output:
        output = Path(args.output)
        if output.suffix.lower() == ".json":
            write_json(output, result.to_dict(), pretty=True)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                "\n\n".join(f"--- page {p.page} ---\n{p.text}" for p in result.pages),
                encoding="utf-8",
            )
        print(f"Texte écrit dans {output}")
        return EXIT_OK

    for page in result.pages:
        print(f"--- page {page.page} ({page.char_count} caractères) ---")
        print(page.text)
    return EXIT_OK


def cmd_parse(args: argparse.Namespace, config: Config) -> int:
    from pathlib import Path

    from bldp.core.classifier import analyze_or_none, decide_extraction_route
    from bldp.core.cleaning.normalizer import clean_pages
    from bldp.core.extraction.ocr import extract_with_route
    from bldp.core.parser.legal_parser import check_numbering, parse_document
    from bldp.jurisdictions.registry import get_ruleset
    from bldp.utils import slugify, write_json

    logger = get_logger()
    pdf_path = Path(args.pdf)
    document_id = slugify(pdf_path.stem)

    analysis = analyze_or_none(pdf_path, document_id, config)
    if analysis is None:
        logger.error("Document illisible : %s", pdf_path)
        return EXIT_ERROR

    route = decide_extraction_route(analysis, config)
    extraction = extract_with_route(
        pdf_path, document_id, route, config, analysis.pages_needing_ocr
    )
    pages, cleaning = clean_pages(extraction.pages, config, document_id)
    result = parse_document(pages, document_id, config, get_ruleset(config), pdf_path.name)
    anomalies = check_numbering(result.articles)

    if args.output:
        write_json(
            args.output,
            {
                "document_id": document_id,
                "route": route,
                "analysis": analysis.to_dict(),
                "cleaning": cleaning.to_dict(),
                "structure": [n.to_dict() for n in result.structure],
                "articles": [a.to_dict() for a in result.articles],
                "numbering_anomalies": anomalies,
                "warnings": result.warnings,
            },
        )
        print(f"Résultat écrit dans {args.output}")
        return EXIT_OK

    print(f"Document   : {document_id}  ({analysis.pages} pages, route « {route} »)")
    print(f"Structure  : {len(result.structure)} subdivision(s)")
    print(f"Articles   : {len(result.articles)}")
    print()

    for node in result.structure:
        print(f"{'  ' * node.depth}{node.label}")
    print()

    for article in result.articles:
        context = " > ".join(article.hierarchy_path) or "(racine)"
        print(f"[p.{article.page_start}] {article.label[:70]}")
        print(f"    contexte : {context}")
        print(f"    alinéas  : {len(article.alineas)}   {len(article.text)} caractères")
        for warning in article.warnings:
            print(f"    ⚠ {warning}")
        if args.full:
            print(f"    {article.text}")
    if anomalies:
        print("\nAnomalies de numérotation :")
        for anomaly in anomalies:
            print(f"  ⚠ {anomaly}")
    for warning in (*result.warnings, *cleaning.warnings, *extraction.warnings):
        print(f"  ⚠ {warning}")
    return EXIT_OK


def _progress_printer(quiet: bool):
    """Affiche l'avancement sur une ligne, sans polluer les journaux."""
    if quiet:
        return None

    def report(rank: int, total: int, document_id: str, stage: str) -> None:
        print(f"  [{rank}/{total}] {stage:12} {document_id}", file=sys.stderr)

    return report


def _print_run_summary(result) -> None:
    """Bilan lisible d'une exécution, au format du §26."""
    report = result.report
    if report is None:
        return

    print()
    print("=" * 64)
    print(f"  {report.total} document(s) traité(s)")
    print(f"    {report.succeeded:4d} réussi(s)")
    print(f"    {report.review_required:4d} nécessite(nt) une vérification")
    print(f"    {report.failed:4d} en échec")
    if report.skipped_duplicates:
        print(f"    {report.skipped_duplicates:4d} doublon(s) signalé(s)")
    print("=" * 64)

    for document in result.review_required:
        issues = ", ".join(
            issue.code for issue in document.quality.issues if issue.severity != "info"
        )
        print(
            f"  À VÉRIFIER  {document.document_id:35} "
            f"score {document.quality.score:.2f}  {issues[:60]}"
        )
    for document in result.failed:
        print(f"  ÉCHEC       {document.document_id:35} {'; '.join(document.errors)[:60]}")

    if result.exports:
        print("\nFichiers produits :")
        for name, path in sorted(result.exports.items()):
            print(f"  {name:22} {path}")


def cmd_pipeline(args: argparse.Namespace, config: Config) -> int:
    from bldp.core.loader import LoaderError
    from bldp.pipeline import run_pipeline

    logger = get_logger()
    source = args.input or config.path("input")

    do_embeddings: bool | None = None
    if args.embed:
        do_embeddings = True
    elif args.no_embed:
        do_embeddings = False

    try:
        result = run_pipeline(
            source,
            config,
            limit=args.limit,
            do_export=not args.no_export,
            do_embeddings=do_embeddings,
            progress=_progress_printer(args.quiet),
        )
    except LoaderError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR

    _print_run_summary(result)
    if result.embeddings_count:
        print(f"\n{result.embeddings_count} vecteur(s) générés.")
        if result.index_path:
            print(f"Index vectoriel : {result.index_path}")

    # Un lot dont tous les documents ont échoué est un échec ; sinon, le
    # pipeline a rempli son office même s'il reste des documents à vérifier.
    report = result.report
    if report and report.total and report.failed == report.total:
        return EXIT_ERROR
    return EXIT_OK


def cmd_process(args: argparse.Namespace, config: Config) -> int:
    from bldp.core.loader import LoaderError
    from bldp.pipeline import process_only

    logger = get_logger()
    try:
        result = process_only(
            args.input or config.path("input"),
            config,
            limit=args.limit,
            progress=_progress_printer(args.quiet),
        )
    except LoaderError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR

    _print_run_summary(result)
    print(f"\nDocuments traités écrits dans {config.path('processed')}")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace, config: Config) -> int:
    from bldp.models import ValidationStatus

    database = _open_database(config)
    if database is None:
        return EXIT_ERROR

    with database:
        if args.set_status:
            if not args.document:
                print("--set-status requiert --document", file=sys.stderr)
                return EXIT_ERROR
            if database.get_document_row(args.document) is None:
                print(f"Document inconnu : {args.document}", file=sys.stderr)
                return EXIT_ERROR
            database.set_validation(args.document, ValidationStatus(args.set_status), args.note)
            print(f"{args.document} : validation = {args.set_status}")
            return EXIT_OK

        if args.document:
            return _show_for_validation(database, args.document, args.article)

        return _list_for_validation(database)


def _list_for_validation(database) -> int:
    """Liste les documents à examiner, du plus problématique au moins."""
    rows = database.connection.execute(
        """SELECT d.document_id, d.title, d.validation, q.score, q.status,
                  q.articles_detected, q.possible_errors
           FROM documents d LEFT JOIN quality_reports q USING (document_id)
           ORDER BY COALESCE(q.score, 0) ASC"""
    ).fetchall()

    if not rows:
        print("Aucun document en base.")
        return EXIT_OK

    print(f"{'DOCUMENT':32} {'SCORE':>6} {'QUALITÉ':>16} {'ART.':>5} {'ANOM.':>6}  VALIDATION")
    print("-" * 92)
    for row in rows:
        score = f"{row['score']:.2f}" if row["score"] is not None else "  —"
        print(
            f"{row['document_id'][:32]:32} {score:>6} {str(row['status'] or '—'):>16} "
            f"{row['articles_detected'] or 0:>5} {row['possible_errors'] or 0:>6}  "
            f"{row['validation']}"
        )
    print(
        "\nExaminer un document : python -m bldp validate --document <id>\n"
        "Décider             : python -m bldp validate --document <id> "
        "--set-status valide|a_verifier|rejete"
    )
    return EXIT_OK


def _show_for_validation(database, document_id: str, article_id: str | None) -> int:
    """Affiche original / texte nettoyé / article structuré côte à côte (§16)."""
    document = database.get_document_row(document_id)
    if document is None:
        print(f"Document inconnu : {document_id}", file=sys.stderr)
        return EXIT_ERROR

    quality = database.get_quality(document_id)
    print(f"Document   : {document['title'] or document_id}")
    print(f"Fichier    : {document['source_path']}")
    print(f"Type/numéro: {document['type']} {document['number'] or ''}".rstrip())
    print(f"Validation : {document['validation']}")
    if quality:
        print(f"Qualité    : {quality['score']:.2f} ({quality['status']})")
        for issue in database.connection.execute(
            "SELECT code, severity, message FROM quality_issues WHERE document_id = ?",
            (document_id,),
        ):
            print(f"    [{issue['severity']:7}] {issue['message']}")

    articles = (
        [database.get_article(article_id)]
        if article_id
        else database.get_articles(document_id)
    )
    articles = [a for a in articles if a is not None]
    if not articles:
        print("\nAucun article à afficher.")
        return EXIT_OK

    for article in articles[:20]:
        page = database.get_page(document_id, article["page_start"])
        print("\n" + "=" * 72)
        print(f"ARTICLE {article['article_number']}  (page {article['page_start']})")
        print("-" * 72)
        if page:
            print("① Page d'origine (texte brut extrait) :")
            print((page["raw_text"] or "")[:700])
            print("\n② Page après nettoyage :")
            print((page["text"] or "")[:700])
        print("\n③ Article structuré :")
        print(article["text"][:900])
    if len(articles) > 20:
        print(f"\n… {len(articles) - 20} article(s) supplémentaire(s) non affiché(s).")
    return EXIT_OK


def cmd_embed(args: argparse.Namespace, config: Config) -> int:
    from bldp.core.chunking import chunking_stats
    from bldp.core.embeddings import (
        EmbeddingError,
        EmbeddingsUnavailableError,
        check_embeddings_ready,
        embed_chunks,
    )
    from bldp.core.storage.sqlite_store import LegalDatabase
    from bldp.core.vectorstore import index_embeddings

    logger = get_logger()
    if args.model:
        config = config.with_overrides({"embeddings": {"model": args.model}})
    config = config.with_overrides({"embeddings": {"enabled": True}})

    database = _open_database(config)
    if database is None:
        return EXIT_ERROR

    with database:
        chunks = _rebuild_chunks_from_database(database, config)
        if not chunks:
            print("Aucun fragment à indexer : le corpus est vide.", file=sys.stderr)
            return EXIT_ERROR

        stats = chunking_stats(chunks)
        print(
            f"{stats['count']} fragment(s) — "
            f"{stats['mean_chars']} caractères en moyenne "
            f"(min {stats['min_chars']}, max {stats['max_chars']})"
        )
        database.save_chunks(chunks)

        if args.dry_run:
            print("--dry-run : aucun vecteur généré.")
            return EXIT_OK

        ready, problems = check_embeddings_ready(config)
        if not ready:
            for problem in problems:
                print(f"  -> {problem}", file=sys.stderr)
            return EXIT_ERROR

        try:
            records = embed_chunks(chunks, config, show_progress=not args.quiet)
        except (EmbeddingsUnavailableError, EmbeddingError) as exc:
            logger.error("%s", exc)
            return EXIT_ERROR

        database.save_embeddings(records)
        index_path = index_embeddings(records, config)

    print(f"{len(records)} vecteur(s) générés (dimension {records[0].dimension}).")
    if index_path:
        print(f"Index vectoriel : {index_path}")
    else:
        print("Index vectoriel non construit (voir les avertissements ci-dessus).")
    return EXIT_OK


def _rebuild_chunks_from_database(database, config: Config):
    """Reconstruit les fragments à partir des articles enregistrés."""
    import json

    from bldp.models import Chunk

    chunks: list[Chunk] = []
    max_chars = int(config.get("chunking.max_chars", 1500))

    for document in database.list_documents():
        for article in database.get_articles(document["document_id"]):
            text = (article["text"] or "").strip()
            if not text:
                continue
            hierarchy = json.loads(article["hierarchy_json"] or "[]")
            # Découpage simple : l'article entier s'il tient, sinon par alinéas.
            bodies = [text]
            if len(text) > max_chars * 1.2:
                alineas = [a["text"] for a in database.get_alineas(article["article_id"])]
                if len(alineas) > 1:
                    bodies = alineas
            for offset, body in enumerate(bodies):
                chunks.append(
                    Chunk(
                        chunk_id=f"{article['article_id']}_chunk_{offset:03d}",
                        document_id=article["document_id"],
                        text=body,
                        article_id=article["article_id"],
                        article_number=article["article_number"],
                        position=len(chunks),
                        page=article["page_start"],
                        title=article["title"],
                        chapter=article["chapter"],
                        section=article["section"],
                        hierarchy_path=hierarchy,
                        strategy="article",
                        metadata={
                            "document_title": document["title"],
                            "document_number": document["number"],
                            "source_file": article["source_file"],
                        },
                    )
                )
    return chunks


def cmd_search(args: argparse.Namespace, config: Config) -> int:
    database = _open_database(config)
    if database is None:
        return EXIT_ERROR

    if args.vector:
        return _vector_search(args, config)

    with database:
        rows = database.search_articles(args.query, limit=args.top)
        if not rows:
            print("Aucun résultat.")
            return EXIT_OK
        for row in rows:
            print(f"\n[{row['article_id']}]  article {row['article_number']}")
            print(f"  {row['extrait'][:300]}")
        print(f"\n{len(rows)} résultat(s). Détail : python -m bldp trace <article_id>")
    return EXIT_OK


def _vector_search(args: argparse.Namespace, config: Config) -> int:
    from bldp.core.embeddings import EmbeddingError, EmbeddingsUnavailableError, embed_query
    from bldp.core.vectorstore import FaissStore, VectorStoreError, VectorStoreUnavailableError

    logger = get_logger()
    index_path = Path(str(config.get("vectorstore.index_path", "data/embeddings/faiss.index")))
    if not index_path.is_absolute():
        index_path = config.root / index_path

    try:
        store = FaissStore.load(index_path)
        vector = embed_query(args.query, config)
        hits = store.search(vector, top_k=args.top)
    except (VectorStoreUnavailableError, VectorStoreError, EmbeddingsUnavailableError,
            EmbeddingError) as exc:
        logger.error("Recherche vectorielle impossible : %s", exc)
        print("Repli conseillé : python -m bldp search <requête> (plein texte)", file=sys.stderr)
        return EXIT_ERROR

    if not hits:
        print("Aucun résultat.")
        return EXIT_OK
    for hit in hits:
        print(f"\n#{hit.rank}  score {hit.score:.3f}  [{hit.article_id or hit.chunk_id}]")
        print(f"  {hit.text[:300]}")
    return EXIT_OK


def cmd_export(args: argparse.Namespace, config: Config) -> int:
    from bldp.core.storage.exporters import export_all

    if args.formats:
        config = config.with_overrides({"export": {"formats": args.formats}})

    database = _open_database(config)
    if database is None:
        return EXIT_ERROR

    with database:
        documents = _rebuild_documents(database)

    if not documents:
        print("Aucun document en base à exporter.", file=sys.stderr)
        return EXIT_ERROR

    produced = export_all(documents, config, output_dir=args.output)
    print(f"{len(documents)} document(s) exporté(s) :")
    for name, path in sorted(produced.items()):
        print(f"  {name:22} {path}")
    return EXIT_OK


def _rebuild_documents(database):
    """Reconstruit des objets Document depuis la base, pour réexport."""
    import json

    from bldp.core.storage.sqlite_store import rebuild_metadata
    from bldp.models import (
        Alinea, Article, Document, ExtractionMethod, ExtractionResult, Page,
        QualityReport, QualityStatus, SourceFile, ValidationStatus,
    )

    documents = []
    for row in database.list_documents():
        document_id = row["document_id"]
        source = SourceFile(
            document_id=document_id,
            source_path=row["source_path"] or "",
            filename=row["filename"] or f"{document_id}.pdf",
            extension=".pdf",
            size_bytes=row["size_bytes"] or 0,
            file_hash=row["file_hash"] or "",
            ingested_at=row["retrieved_at"] or "",
            category=row["category"] or "autres",
            raw_path=row["raw_path"],
        )
        pages = [
            Page(
                document_id=document_id,
                page=page["page"],
                text=page["text"] or "",
                source_file=page["source_file"] or source.filename,
                raw_text=page["raw_text"],
                method=ExtractionMethod(page["method"]) if page["method"] else ExtractionMethod.NATIVE,
                ocr_confidence=page["ocr_confidence"],
                warnings=json.loads(page["warnings_json"] or "[]"),
            )
            for page in database.get_pages(document_id)
        ]
        articles = []
        for article in database.get_articles(document_id):
            articles.append(
                Article(
                    article_id=article["article_id"],
                    document_id=document_id,
                    article_number=article["article_number"],
                    text=article["text"] or "",
                    label=article["label"] or "",
                    position=article["position"] or 0,
                    page_start=article["page_start"] or 0,
                    page_end=article["page_end"] or 0,
                    char_start=article["char_start"] or 0,
                    char_end=article["char_end"] or 0,
                    title=article["title"],
                    chapter=article["chapter"],
                    section=article["section"],
                    subsection=article["subsection"],
                    annexe=article["annexe"],
                    hierarchy_path=json.loads(article["hierarchy_json"] or "[]"),
                    numeric_value=article["numeric_value"],
                    source_file=article["source_file"] or source.filename,
                    warnings=json.loads(article["warnings_json"] or "[]"),
                    alineas=[
                        Alinea(index=a["idx"], text=a["text"] or "", number=a["number"])
                        for a in database.get_alineas(article["article_id"])
                    ],
                )
            )

        quality_row = database.get_quality(document_id)
        quality = (
            QualityReport(
                document_id=document_id,
                score=quality_row["score"] or 0.0,
                ocr_quality=quality_row["ocr_quality"],
                text_quality=quality_row["text_quality"] or 0.0,
                structure_quality=quality_row["structure_quality"] or 0.0,
                pages=quality_row["pages"] or 0,
                empty_pages=quality_row["empty_pages"] or 0,
                duplicate_pages=quality_row["duplicate_pages"] or 0,
                missing_pages=quality_row["missing_pages"] or 0,
                articles_detected=quality_row["articles_detected"] or 0,
                numbering_gaps=json.loads(quality_row["numbering_json"] or "[]"),
                possible_errors=quality_row["possible_errors"] or 0,
                status=QualityStatus(quality_row["status"]) if quality_row["status"] else QualityStatus.OK,
            )
            if quality_row
            else None
        )

        documents.append(
            Document(
                document_id=document_id,
                source=source,
                metadata=rebuild_metadata(row),
                extraction=ExtractionResult(
                    document_id=document_id,
                    source_file=source.filename,
                    method=ExtractionMethod(row["extraction_method"])
                    if row["extraction_method"]
                    else ExtractionMethod.NATIVE,
                    pages=pages,
                ),
                articles=articles,
                quality=quality,
                validation=ValidationStatus(row["validation"] or "en_attente"),
                validation_note=row["validation_note"] or "",
                text_hash=row["text_hash"],
                processed_at=row["processed_at"],
                pipeline_version=row["pipeline_version"] or "",
                errors=json.loads(row["errors_json"] or "[]"),
            )
        )
    return documents


def cmd_serve(args: argparse.Namespace, config: Config) -> int:
    from bldp.web.app import WebUnavailableError, serve

    logger = get_logger()
    try:
        serve(config, host=args.host, port=args.port, reload=args.reload)
    except WebUnavailableError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    return EXIT_OK


def _open_database(config: Config):
    """Ouvre la base du corpus, ou explique pourquoi c'est impossible."""
    from bldp.core.storage.sqlite_store import LegalDatabase

    path = config.path("database")
    if not path.exists():
        print(
            f"Aucune base à {path}.\n"
            "Lancez d'abord : python -m bldp pipeline ./input",
            file=sys.stderr,
        )
        return None
    return LegalDatabase(path, create=False)


def cmd_stats(args: argparse.Namespace, config: Config) -> int:
    import json

    database = _open_database(config)
    if database is None:
        return EXIT_ERROR
    with database:
        print(json.dumps(database.stats(), ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_trace(args: argparse.Namespace, config: Config) -> int:
    import json

    database = _open_database(config)
    if database is None:
        return EXIT_ERROR

    with database:
        trace = database.trace_article(args.article_id)
        if trace is None:
            print(f"Article inconnu : {args.article_id}", file=sys.stderr)
            return EXIT_ERROR

        article, document, page = trace["article"], trace["document"], trace["page"]
        print(f"Article        : {article['article_number']}  ({article['article_id']})")
        print(f"Document       : {document['title'] or document['document_id']}")
        print(f"  type/numéro  : {document['type']} {document['number'] or ''}".rstrip())
        print(f"  contexte     : {' > '.join(json.loads(article['hierarchy_json'] or '[]')) or '(racine)'}")
        print(f"  pages        : {article['page_start']}–{article['page_end']}")
        print(f"  fichier      : {trace['source_path']}")
        print(f"  validation   : {document['validation']}")
        print("\n--- texte de l'article ---")
        print(article["text"])
        if trace["alineas"]:
            print(f"\n--- {len(trace['alineas'])} alinéa(s) ---")
            for alinea in trace["alineas"]:
                print(f"  [{alinea['idx']}] {alinea['number'] or ''} {alinea['text'][:120]}")
        if page:
            print(f"\n--- page {page['page']} d'origine (texte nettoyé) ---")
            print(page["text"][:2000])
    return EXIT_OK



def _parse_page_selection(spec: str | None) -> list[int] | None:
    """Interprète une sélection de pages ``"1,3,5-8"`` en liste 1-based."""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def _force_utf8_output() -> None:
    """Évite les accents mutilés sur les consoles Windows en cp1252."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # flux redirigé ou non reconfigurable
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    try:
        config = load_config(
            path=args.config, overrides=args.overrides, root=getattr(args, "root", None)
        )
    except ConfigError as exc:
        print(f"[ERREUR] Configuration : {exc}", file=sys.stderr)
        return EXIT_ERROR

    level = args.log_level or ("ERROR" if args.quiet else config.get("logging.level", "INFO"))
    setup_logging(
        level=level,
        to_file=bool(config.get("logging.to_file", True)),
        file_path=config.path("logs") / "bldp.log",
        json_lines=bool(config.get("logging.json_lines", False)),
    )
    logger = get_logger()

    handler: Callable[[argparse.Namespace, Config], int] = args.func
    try:
        return handler(args, config)
    except KeyboardInterrupt:
        logger.warning("Interruption utilisateur.")
        return 130
    except Exception as exc:  # noqa: BLE001 - la CLI ne doit jamais tracer brut
        logger.error("Échec de la commande %s : %s", args.command, exc, exc_info=True)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
