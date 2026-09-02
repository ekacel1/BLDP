"""Accès au modèle de relecture, et ce qui l'encadre.

Le cahier des charges pose une règle nette (§27) : **aucun document ne quitte
la machine** tant que ``privacy.allow_external_calls`` vaut ``false``. Relire
avec un modèle distant y contrevient par nature — envoyer un texte à une API,
c'est le publier hors du poste.

Ce module ne contourne pas la règle, il l'instrumente :

* l'appel est refusé tant que l'autorisation n'est pas donnée **explicitement**
  dans la configuration — jamais par défaut, jamais par surprise ;
* la clé se lit dans l'environnement, jamais dans un fichier de configuration
  qui finirait dans un dépôt ;
* chaque appel est journalisé : quel document, combien de jetons, quel coût
  estimé. Ce qui sort de la machine doit être connu et chiffrable.

Rien ici ne décide du contenu : ce module transporte, il ne juge pas.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from bldp.config import Config
from bldp.logging_setup import get_logger

logger = get_logger("review.client")


class ReviewUnavailableError(RuntimeError):
    """La relecture assistée n'est pas utilisable en l'état."""


class ReviewCallError(RuntimeError):
    """L'appel au modèle a échoué."""


#: Variable d'environnement portant la clé.
#:
#: **Jamais dans un fichier de configuration.** ``config/local.yaml`` finit
#: dans un dépôt ou dans une sauvegarde ; une clé d'API n'y a pas sa place.
API_KEY_ENV = "ANTHROPIC_API_KEY"

#: Modèle par défaut. La relecture juridique demande du raisonnement, pas de
#: la vitesse : c'est le mauvais endroit pour économiser.
DEFAULT_MODEL = "claude-opus-5"

#: Tarifs indicatifs, en dollars par million de jetons, pour estimer une
#: dépense avant de la faire. Ils évoluent : ce sont des ordres de grandeur.
PRICING_USD: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass
class CallReport:
    """Ce qu'a coûté un appel — en jetons, en temps, en argent."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    duration_seconds: float = 0.0
    stop_reason: str = ""

    @property
    def estimated_usd(self) -> float:
        entree, sortie = PRICING_USD.get(self.model, (0.0, 0.0))
        return (
            self.input_tokens * entree + self.output_tokens * sortie
        ) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "duration_seconds": round(self.duration_seconds, 2),
            "stop_reason": self.stop_reason,
            "estimated_usd": round(self.estimated_usd, 4),
        }


def review_available() -> bool:
    """Vrai si le paquet ``anthropic`` est installé."""
    try:
        import anthropic  # noqa: F401

        return True
    except ImportError:
        return False


def api_key_present() -> bool:
    """Vrai si une clé est visible dans l'environnement."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def check_ready(config: Config) -> tuple[bool, list[str]]:
    """Vérifie que la relecture assistée est réellement utilisable.

    Returns:
        ``(prêt, obstacles)`` — chaque obstacle dit quoi faire, en clair.
        Le diagnostic ne prétend jamais qu'un dispositif fonctionne quand il
        lui manque une pièce.
    """
    obstacles: list[str] = []

    if not config.get("privacy.allow_external_calls", False):
        obstacles.append(
            "les appels externes sont interdits par la configuration "
            "(privacy.allow_external_calls = false). Relire avec un modèle "
            "distant envoie le texte des documents hors de cette machine : "
            "cette autorisation doit être donnée sciemment."
        )
    if not config.get("ai_review.enabled", False):
        obstacles.append(
            "la relecture assistée est désactivée (ai_review.enabled = false)."
        )
    if not review_available():
        obstacles.append(
            'le paquet anthropic est absent : pip install -e ".[review]"'
        )
    if not api_key_present():
        obstacles.append(
            f"aucune clé dans ${API_KEY_ENV}. Définissez-la dans "
            "l'environnement — jamais dans un fichier de configuration."
        )
    return not obstacles, obstacles


class ReviewClient:
    """Enveloppe minimale autour de l'API, avec ses garde-fous.

    S'utilise comme gestionnaire de contexte ::

        with ReviewClient(config) as client:
            donnees, rapport = client.ask(systeme, message, schema)
    """

    def __init__(self, config: Config) -> None:
        pret, obstacles = check_ready(config)
        if not pret:
            raise ReviewUnavailableError(
                "Relecture assistée indisponible :\n  - " + "\n  - ".join(obstacles)
            )

        import anthropic

        self.config = config
        self.model = str(config.get("ai_review.model", DEFAULT_MODEL))
        self.effort = str(config.get("ai_review.effort", "high"))
        self.max_tokens = int(config.get("ai_review.max_tokens", 16000))
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            timeout=float(config.get("ai_review.timeout_seconds", 300)),
            max_retries=int(config.get("ai_review.max_retries", 3)),
        )
        self.calls: list[CallReport] = []

    def __enter__(self) -> "ReviewClient":
        return self

    def __exit__(self, *exc_info) -> None:
        if self.calls:
            total = sum(c.estimated_usd for c in self.calls)
            logger.info(
                "Relecture assistée : %d appel(s), coût estimé %.4f $.",
                len(self.calls), total,
            )

    @property
    def total_estimated_usd(self) -> float:
        return sum(call.estimated_usd for call in self.calls)

    def ask(
        self,
        system: str,
        message: "str | list[dict]",
        schema: dict,
        label: str = "",
    ) -> tuple[dict, CallReport]:
        """Pose une question au modèle et renvoie sa réponse structurée.

        Args:
            system: instructions permanentes — mises en cache d'un appel à
                l'autre, car identiques pour tous les documents.
            message: le document à relire. Une chaîne, ou une liste de blocs
                de contenu lorsque les images des pages sont jointes.
            schema: schéma JSON contraignant la réponse.
            label: identifiant du document, pour le journal.

        Raises:
            ReviewCallError: échec de l'appel, refus du modèle, ou réponse
                inexploitable. Aucun de ces cas ne renvoie de données
                partielles : mieux vaut ne rien relire que relire à moitié.
        """
        depart = time.perf_counter()
        try:
            reponse = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system,
                        # Les instructions ne varient pas d'un document à
                        # l'autre : les mettre en cache réduit nettement le
                        # coût d'un lot.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": message}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except self._anthropic.AuthenticationError as exc:
            raise ReviewCallError(
                f"clé d'API refusée. Vérifiez ${API_KEY_ENV}."
            ) from exc
        except self._anthropic.RateLimitError as exc:
            raise ReviewCallError(
                "limite de débit atteinte malgré les tentatives. Réessayez "
                "plus tard, ou réduisez la taille du lot."
            ) from exc
        except self._anthropic.APIConnectionError as exc:
            raise ReviewCallError(f"connexion impossible : {exc}") from exc
        except self._anthropic.APIStatusError as exc:
            raise ReviewCallError(
                f"erreur {exc.status_code} : {getattr(exc, 'message', exc)}"
            ) from exc

        rapport = CallReport(
            model=self.model,
            input_tokens=getattr(reponse.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(reponse.usage, "output_tokens", 0) or 0,
            cached_tokens=getattr(reponse.usage, "cache_read_input_tokens", 0) or 0,
            duration_seconds=time.perf_counter() - depart,
            stop_reason=reponse.stop_reason or "",
        )
        self.calls.append(rapport)
        logger.info(
            "Relecture %s : %d jetons entrants (%d en cache), %d sortants, "
            "%.1fs, ~%.4f $.",
            label or "?", rapport.input_tokens, rapport.cached_tokens,
            rapport.output_tokens, rapport.duration_seconds, rapport.estimated_usd,
        )

        if reponse.stop_reason == "refusal":
            details = getattr(reponse, "stop_details", None)
            raise ReviewCallError(
                "le modèle a refusé de traiter ce document"
                + (f" ({details.category})" if details else "")
            )
        if reponse.stop_reason == "max_tokens":
            raise ReviewCallError(
                "réponse tronquée par max_tokens : le document est trop long "
                "pour un seul appel. Augmentez ai_review.max_tokens."
            )

        texte = next(
            (bloc.text for bloc in reponse.content if bloc.type == "text"), ""
        )
        try:
            return json.loads(texte), rapport
        except json.JSONDecodeError as exc:
            raise ReviewCallError(
                f"réponse illisible : {exc}. Rien n'a été appliqué."
            ) from exc
