"""Event-level interpretation settings."""

from __future__ import annotations

from eventyay.base.models import Event

SETTING_IS_ENABLED = "interpretation_is_enabled"
SETTING_USE_PLUGIN_STREAMS = "interpretation_use_plugin_streams"


def is_interpretation_enabled(event: Event) -> bool:
    return event.settings.get(SETTING_IS_ENABLED, default=True, as_type=bool)


def use_plugin_language_streams(event: Event) -> bool:
    if not is_interpretation_enabled(event):
        return False
    return event.settings.get(SETTING_USE_PLUGIN_STREAMS, default=False, as_type=bool)
