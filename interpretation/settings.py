"""Per-event SUSI connection settings stored in the event settings backend."""

from __future__ import annotations

from eventyay.base.models import Event

from .susi import SusiClient

SETTING_BASE_URL = "interpretation_base_url"
SETTING_AUTH_TOKEN = "interpretation_auth_token"
SETTING_IS_ENABLED = "interpretation_is_enabled"


def get_base_url(event: Event) -> str:
    return event.settings.get(SETTING_BASE_URL, default="", as_type=str)


def get_auth_token(event: Event) -> str:
    return event.settings.get(SETTING_AUTH_TOKEN, default="", as_type=str)


def is_interpretation_enabled(event: Event) -> bool:
    return event.settings.get(SETTING_IS_ENABLED, default=False, as_type=bool)


def is_susi_configured(event: Event) -> bool:
    return bool(
        is_interpretation_enabled(event) and get_base_url(event) and get_auth_token(event)
    )


def get_susi_client(event: Event) -> SusiClient:
    return SusiClient(get_base_url(event), get_auth_token(event))
