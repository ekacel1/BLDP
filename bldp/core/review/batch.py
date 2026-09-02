"""Relire un lot de documents : d'abord estimer, ensuite envoyer.

Un lot de relecture engage deux choses qu'on ne récupère pas : de l'argent, et
la sortie de textes hors de la machine (§27). Ce module sépare donc
explicitement les deux temps.

:func:`plan_review` ne fait **aucun appel**. Elle mesure ce que chaque document
représente, écarte ceux qui ne passeront pas, et chiffre la dépense. C'est ce
qu'on montre avant de demander le feu vert.

:func:`run_review` exécute le plan validé. Elle ne relit jamais un document que
le plan a écarté : ce qui a été montré à l'utilisateur est exactement ce qui
part.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from bldp.config import Config
from bldp.logging_setup import get_logger
from bldp.models import Document
from bldp.core.review.client import PRICING_USD, ReviewCallError, ReviewClient
from bldp.core.review.reviewer import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    ReviewResult,
    build_message,
    review_document,
)

logger = get_logger("review.batch")

#: Rapport approximatif caractères/jetons pour du français. Sert uniquement à
#: annoncer un ordre de grandeur *avant* l'appel ; le coût réel est mesuré
#: après, sur les jetons réellement consommés.
CHARS_PER_TOKEN = 3.6

#: Longueur ordinaire d'un compte rendu de relecture, en jetons. Un document
#: propre tient en quelques centaines ; un document très abîmé en quelques
#: milliers. ``ai_review.max_tokens`` reste la limite dure.
TYPICAL_OUTPUT_TOKENS = 2500


@dataclass
class PlannedDocument:
    """Un document du lot, tel qu'il se présente avant tout appel."""

    document_id: str
    title: str = ""
    pages: int = 0
    articles: int = 0
    chars: int = 0
    images: int = 0
    image_tokens: int = 0
    skip_reason: str = ""

    @property
    def eligible(self) -> bool:
        return not self.skip_reason

    @property
    def estimated_input_tokens(self) -> int:
        texte = int((self.chars + len(SYSTEM_PROMPT)) / CHARS_PER_TOKEN)
        return texte + self.image_tokens

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "pages": self.pages,
            "articles": self.articles,
            "chars": self.chars,
            "images": self.images,
            "image_tokens": self.image_tokens,
            "eligible": self.eligible,
            "skip_reason": self.skip_reason,
        }


@dataclass
class ReviewPlan:
    """Ce qui partirait, ce qui resterait, et ce que cela coûterait."""

    model: str
    documents: list[PlannedDocument] = field(default_factory=list)
    max_output_tokens: int = 16000
    #: Les images des pages sont-elles jointes ? Sans elles, la relecture
    #: compare l'extraction à elle-même : c'est un mode dégradé, pas le mode
    #: normal, et l'annonce doit le dire.
    collation: bool = True

    @property
    def eligible(self) -> list[PlannedDocument]:
        return [d for d in self.documents if d.eligible]

    @property
    def skipped(self) -> list[PlannedDocument]:
        return [d for d in self.documents if not d.eligible]

    @property
    def input_usd(self) -> float:
        """Coût de ce qui part. C'est la part connue : le texte est mesuré."""
        entree, _ = PRICING_USD.get(self.model, (0.0, 0.0))
        return sum(d.estimated_input_tokens for d in self.eligible) * entree / 1_000_000

    @property
    def estimated_usd(self) -> float:
        """Coût attendu, réponses de taille ordinaire.

        La longueur d'une réponse n'est pas connue d'avance ; celle d'un
        compte rendu de relecture l'est approximativement. Ce chiffre-là est
        l'ordre de grandeur à annoncer, et :attr:`ceiling_usd` la limite
        au-delà de laquelle rien ne peut aller.
        """
        _, sortie = PRICING_USD.get(self.model, (0.0, 0.0))
        jetons = TYPICAL_OUTPUT_TOKENS * len(self.eligible)
        return self.input_usd + jetons * sortie / 1_000_000

    @property
    def ceiling_usd(self) -> float:
        """Plafond absolu : toutes les réponses au maximum autorisé."""
        _, sortie = PRICING_USD.get(self.model, (0.0, 0.0))
        jetons = self.max_output_tokens * len(self.eligible)
        return self.input_usd + jetons * sortie / 1_000_000

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "documents": [d.to_dict() for d in self.documents],
            "retenus": len(self.eligible),
            "ecartes": len(self.skipped),
            "collation_sur_image": self.collation,
            "images": sum(d.images for d in self.eligible),
            "cout_estime_usd": round(self.estimated_usd, 4),
            "cout_plafond_usd": round(self.ceiling_usd, 4),
        }


def plan_review(documents: Sequence[Document], config: Config) -> ReviewPlan:
    """Prépare un lot sans rien envoyer.

    Un document est écarté dès qu'une collation honnête est impossible :
    aucune page enregistrée, PDF d'origine introuvable, ou texte trop long
    pour tenir dans un seul appel. Écarter coûte un document ; relire sans
    référence coûte la confiance dans tous les autres.
    """
    from bldp.core.review.page_images import PageImageError, estimate_images

    max_chars = int(config.get("ai_review.max_chars", 120_000))
    avec_images = bool(config.get("ai_review.send_page_images", True))
    images_exigees = bool(config.get("ai_review.require_page_images", True))
    plan = ReviewPlan(
        model=str(config.get("ai_review.model", "claude-opus-5")),
        max_output_tokens=int(config.get("ai_review.max_tokens", 16000)),
        collation=avec_images,
    )

    for document in documents:
        entree = PlannedDocument(
            document_id=document.document_id,
            title=(document.metadata.title or "")[:70],
            pages=len(document.pages),
            articles=len(document.articles),
        )
        plan.documents.append(entree)

        if not document.pages:
            entree.skip_reason = (
                "aucune page enregistrée : rien contre quoi vérifier les articles"
            )
            continue

        entree.chars = sum(len(page.text) for page in document.pages)
        if entree.chars > max_chars:
            entree.skip_reason = (
                f"texte trop long ({entree.chars} caractères pour une limite "
                f"de {max_chars})"
            )
            continue

        if not avec_images:
            # Mode texte seul : le message complet est plus verbeux que la
            # somme des pages, on le mesure tel qu'il partira.
            try:
                entree.chars = len(build_message(document, max_chars))
            except ReviewCallError as exc:
                entree.skip_reason = str(exc)
            continue

        try:
            entree.images, entree.image_tokens = estimate_images(
                document,
                max_edge=int(config.get("ai_review.image_max_edge", 1568)),
                max_pages=int(config.get("ai_review.max_image_pages", 100)),
            )
        except PageImageError as exc:
            if images_exigees:
                entree.skip_reason = str(exc)
            else:
                logger.warning(
                    "%s sera relu sans image : %s", document.document_id, exc
                )

    return plan


@dataclass
class BatchOutcome:
    """Le résultat d'un lot relu."""

    results: list[ReviewResult] = field(default_factory=list)
    total_usd: float = 0.0

    @property
    def failed(self) -> list[ReviewResult]:
        return [r for r in self.results if not r.ok]

    @property
    def doubtful(self) -> list[ReviewResult]:
        return [r for r in self.results if r.ok and r.verdict == "douteux"]

    @property
    def corrected(self) -> list[ReviewResult]:
        return [r for r in self.results if r.applied]

    def to_dict(self) -> dict:
        return {
            "documents": [r.to_dict() for r in self.results],
            "relus": len(self.results),
            "echecs": len(self.failed),
            "douteux": len(self.doubtful),
            "corriges": len(self.corrected),
            "cout_reel_usd": round(self.total_usd, 4),
        }


def run_review(
    documents: Sequence[Document],
    config: Config,
    plan: Optional[ReviewPlan] = None,
    on_result: Optional[Callable[[Document, ReviewResult], None]] = None,
) -> BatchOutcome:
    """Relit les documents retenus par le plan, un par un.

    Args:
        documents: les documents chargés, dans l'ordre de traitement.
        config: configuration courante.
        plan: plan issu de :func:`plan_review`. Les documents qu'il écarte ne
            sont pas relus — ce qui a été annoncé est ce qui part.
        on_result: appelé après chaque document, pour enregistrer au fil de
            l'eau. Un lot interrompu garde ainsi ce qu'il a déjà produit.

    L'échec d'un document n'interrompt jamais le lot (§26) : il devient un
    résultat porteur d'erreur, et le suivant est relu.
    """
    plan = plan or plan_review(documents, config)
    ecartes = {d.document_id: d.skip_reason for d in plan.skipped}
    resultat = BatchOutcome()

    with ReviewClient(config) as client:
        for document in documents:
            motif = ecartes.get(document.document_id)
            if motif:
                logger.info("%s écarté : %s", document.document_id, motif)
                resultat.results.append(
                    ReviewResult(document_id=document.document_id, error=motif)
                )
                continue

            revue = review_document(document, config, client=client)
            resultat.results.append(revue)
            if on_result is not None:
                on_result(document, revue)
        resultat.total_usd = client.total_estimated_usd

    return resultat
