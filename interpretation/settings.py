"""Per-event SUSI connection settings stored in the event settings backend."""

from __future__ import annotations

from eventyay.base.models import Event

from .susi import SusiClient

SETTING_BASE_URL = "interpretation_base_url"
SETTING_AUTH_TOKEN = "interpretation_auth_token"
SETTING_SUSI_EMAIL = "interpretation_susi_email"
SETTING_SUSI_NAME = "interpretation_susi_name"
SETTING_IS_ENABLED = "interpretation_is_enabled"


def get_base_url(event: Event) -> str:
    return event.settings.get(SETTING_BASE_URL, default="", as_type=str)


def get_auth_token(event: Event) -> str:
    return event.settings.get(SETTING_AUTH_TOKEN, default="", as_type=str)


def get_susi_email(event: Event) -> str:
    return event.settings.get(SETTING_SUSI_EMAIL, default="", as_type=str)


def get_susi_name(event: Event) -> str:
    return event.settings.get(SETTING_SUSI_NAME, default="", as_type=str)


def is_interpretation_enabled(event: Event) -> bool:
    return event.settings.get(SETTING_IS_ENABLED, default=False, as_type=bool)


def is_susi_configured(event: Event) -> bool:
    return bool(get_base_url(event) and get_auth_token(event))


def save_susi_connection(
    event: Event, *, token: str, email: str = "", name: str = ""
) -> None:
    event.settings.set(SETTING_AUTH_TOKEN, token)
    event.settings.set(SETTING_SUSI_EMAIL, email)
    event.settings.set(SETTING_SUSI_NAME, name)


def disconnect_susi(event: Event) -> None:
    event.settings.set(SETTING_AUTH_TOKEN, "")
    event.settings.set(SETTING_SUSI_EMAIL, "")
    event.settings.set(SETTING_SUSI_NAME, "")


def get_susi_client(event: Event) -> SusiClient:
    return SusiClient(get_base_url(event), get_auth_token(event))
