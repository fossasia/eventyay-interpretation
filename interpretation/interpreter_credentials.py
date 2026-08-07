"""Event-level interpreter credentials stored in event settings."""

from __future__ import annotations

from urllib.parse import urlparse

from eventyay.base.models import Event

from .models import RoomInterpretation
from .susi import SusiClient

SUSI_BACKEND_ID = RoomInterpretation.INTERPRETER_SUSI

SETTING_SUSI_BASE_URL = "interpretation_susi_base_url"
SETTING_SUSI_AUTH_TOKEN = "interpretation_susi_auth_token"
SETTING_SUSI_ACCOUNT_EMAIL = "interpretation_susi_account_email"
SETTING_SUSI_ACCOUNT_NAME = "interpretation_susi_account_name"

LEGACY_SUSI_BASE_URL = "interpretation_base_url"
LEGACY_SUSI_AUTH_TOKEN = "interpretation_auth_token"
LEGACY_SUSI_EMAIL = "interpretation_susi_email"
LEGACY_SUSI_NAME = "interpretation_susi_name"

ROOM_SUSI_BASE_URL = "susi_base_url"
ROOM_SUSI_AUTH_TOKEN = "susi_auth_token"
ROOM_SUSI_ACCOUNT_EMAIL = "susi_account_email"
ROOM_SUSI_ACCOUNT_NAME = "susi_account_name"

INTERPRETER_CREDENTIAL_KEYS = frozenset(
    {
        ROOM_SUSI_BASE_URL,
        ROOM_SUSI_AUTH_TOKEN,
        ROOM_SUSI_ACCOUNT_EMAIL,
        ROOM_SUSI_ACCOUNT_NAME,
    }
)

SUSI_CREDENTIAL_KEYS = INTERPRETER_CREDENTIAL_KEYS


def _event_setting(event: Event, key: str, *, legacy_key: str = "") -> str:
    value = event.settings.get(key, default="", as_type=str)
    if value:
        return value
    if legacy_key:
        return event.settings.get(legacy_key, default="", as_type=str)
    return ""


def get_susi_base_url(event: Event | None) -> str:
    if event is None:
        return ""
    return (
        _event_setting(
            event,
            SETTING_SUSI_BASE_URL,
            legacy_key=LEGACY_SUSI_BASE_URL,
        )
        .strip()
        .rstrip("/")
    )


def get_susi_auth_token(event: Event | None) -> str:
    if event is None:
        return ""
    return _event_setting(
        event,
        SETTING_SUSI_AUTH_TOKEN,
        legacy_key=LEGACY_SUSI_AUTH_TOKEN,
    ).strip()


def get_susi_account_email(event: Event | None) -> str:
    if event is None:
        return ""
    return _event_setting(
        event,
        SETTING_SUSI_ACCOUNT_EMAIL,
        legacy_key=LEGACY_SUSI_EMAIL,
    ).strip()


def get_susi_account_name(event: Event | None) -> str:
    if event is None:
        return ""
    return _event_setting(
        event,
        SETTING_SUSI_ACCOUNT_NAME,
        legacy_key=LEGACY_SUSI_NAME,
    ).strip()


def is_susi_configured(event: Event | None) -> bool:
    return bool(get_susi_base_url(event) and get_susi_auth_token(event))


def is_interpreter_configured(event: Event | None, backend_id: str) -> bool:
    if backend_id == RoomInterpretation.INTERPRETER_NONE:
        return True
    if backend_id == SUSI_BACKEND_ID:
        return is_susi_configured(event)
    return False


def save_susi_credentials(
    event: Event,
    *,
    base_url: str,
    token: str,
    email: str = "",
    name: str = "",
) -> None:
    event.settings.set(SETTING_SUSI_BASE_URL, (base_url or "").strip().rstrip("/"))
    event.settings.set(SETTING_SUSI_AUTH_TOKEN, (token or "").strip())
    event.settings.set(SETTING_SUSI_ACCOUNT_EMAIL, (email or "").strip())
    event.settings.set(SETTING_SUSI_ACCOUNT_NAME, (name or "").strip())
    event.settings.set(LEGACY_SUSI_BASE_URL, "")
    event.settings.set(LEGACY_SUSI_AUTH_TOKEN, "")
    event.settings.set(LEGACY_SUSI_EMAIL, "")
    event.settings.set(LEGACY_SUSI_NAME, "")


def stop_interpreter_sessions(event: Event, backend_id: str) -> None:
    from .room_control import stop_room_session

    interpretations = RoomInterpretation.objects.filter(
        room__event=event,
        interpreter=backend_id,
    ).exclude(backend_session_id="")
    for interpretation in interpretations.select_related("room"):
        stop_room_session(interpretation.room, event)


def clear_susi_credentials(event: Event) -> None:
    stop_interpreter_sessions(event, SUSI_BACKEND_ID)
    for key in (
        SETTING_SUSI_BASE_URL,
        SETTING_SUSI_AUTH_TOKEN,
        SETTING_SUSI_ACCOUNT_EMAIL,
        SETTING_SUSI_ACCOUNT_NAME,
        LEGACY_SUSI_BASE_URL,
        LEGACY_SUSI_AUTH_TOKEN,
        LEGACY_SUSI_EMAIL,
        LEGACY_SUSI_NAME,
    ):
        event.settings.set(key, "")


def clear_interpreter_credentials(event: Event, backend_id: str) -> None:
    if backend_id == SUSI_BACKEND_ID:
        clear_susi_credentials(event)


def susi_account_label(event: Event | None) -> str:
    name = get_susi_account_name(event)
    email = get_susi_account_email(event)
    if name and email:
        return f"{name} ({email})"
    return email or name


def susi_server_host(event: Event | None) -> str:
    base_url = get_susi_base_url(event)
    if not base_url:
        return ""
    return urlparse(base_url).netloc or base_url


def get_susi_client(event: Event | None) -> SusiClient:
    return SusiClient(
        get_susi_base_url(event),
        get_susi_auth_token(event),
    )


def strip_room_credential_keys(config: dict | None) -> dict:
    cleaned = dict(config or {})
    for key in INTERPRETER_CREDENTIAL_KEYS:
        cleaned.pop(key, None)
    return cleaned
