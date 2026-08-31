"""Suivi des documents : tickets, étapes et journal d'activité.

Ce sous-module répond à une question que le pipeline seul ne sait pas traiter :
*où en est-on sur ce document, et qui s'en occupe ?*
"""

from bldp.core.tracking.registry import (
    STAGE_BADGES,
    Stage,
    Ticket,
    TrackingEvent,
    TrackingRegistry,
    allowed_transitions,
    badge_for,
)

__all__ = [
    "STAGE_BADGES",
    "Stage",
    "Ticket",
    "TrackingEvent",
    "TrackingRegistry",
    "allowed_transitions",
    "badge_for",
]
