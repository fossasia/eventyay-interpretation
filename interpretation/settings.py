"""Event-level interpretation settings (feature toggle only)."""

from __future__ import annotations

from eventyay.base.models import Event

SETTING_IS_ENABLED = "interpretation_is_enabled"


def is_interpretation_enabled(event: Event) -> bool:
    return event.settings.get(SETTING_IS_ENABLED, default=True, as_type=bool)
