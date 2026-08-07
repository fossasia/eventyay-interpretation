"""Facade for event-level interpreter credentials and room secret stripping."""

from __future__ import annotations

from eventyay.base.models import Event

from .backends import get_backend, is_registered_interpreter
from .backends.registry import all_room_credential_keys
from .backends.susi_credentials import (
    EVENT_SETTINGS_KEYS,
    LEGACY_SUSI_AUTH_TOKEN,
    LEGACY_SUSI_BASE_URL,
    LEGACY_SUSI_EMAIL,
    LEGACY_SUSI_NAME,
    ROOM_CREDENTIAL_KEYS,
    ROOM_SUSI_ACCOUNT_EMAIL,
    ROOM_SUSI_ACCOUNT_NAME,
    ROOM_SUSI_AUTH_TOKEN,
    ROOM_SUSI_BASE_URL,
    SETTING_SUSI_ACCOUNT_EMAIL,
    SETTING_SUSI_ACCOUNT_NAME,
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_BASE_URL,
    get_susi_auth_token,
    get_susi_base_url,
    get_susi_client,
    is_susi_configured,
    save_susi_credentials,
    susi_account_label,
    susi_server_host,
)
from .models import RoomInterpretation

SUSI_BACKEND_ID = RoomInterpretation.INTERPRETER_SUSI

INTERPRETER_CREDENTIAL_KEYS = all_room_credential_keys()
SUSI_CREDENTIAL_KEYS = ROOM_CREDENTIAL_KEYS


def is_interpreter_configured(event: Event | None, backend_id: str) -> bool:
    return get_backend(backend_id).is_configured(event)


def stop_interpreter_sessions(event: Event, backend_id: str) -> None:
    from .room_control import stop_room_session

    interpretations = RoomInterpretation.objects.filter(
        room__event=event,
        interpreter=backend_id,
    ).exclude(backend_session_id="")
    for interpretation in interpretations.select_related("room"):
        stop_room_session(interpretation.room, event)


def clear_interpreter_credentials(event: Event, backend_id: str) -> None:
    backend = get_backend(backend_id)
    if backend.uses_event_credentials:
        backend.disconnect(event)


def strip_room_credential_keys(config: dict | None) -> dict:
    cleaned = dict(config or {})
    for key in INTERPRETER_CREDENTIAL_KEYS:
        cleaned.pop(key, None)
    return cleaned


__all__ = [
    "EVENT_SETTINGS_KEYS",
    "INTERPRETER_CREDENTIAL_KEYS",
    "LEGACY_SUSI_AUTH_TOKEN",
    "LEGACY_SUSI_BASE_URL",
    "LEGACY_SUSI_EMAIL",
    "LEGACY_SUSI_NAME",
    "ROOM_SUSI_ACCOUNT_EMAIL",
    "ROOM_SUSI_ACCOUNT_NAME",
    "ROOM_SUSI_AUTH_TOKEN",
    "ROOM_SUSI_BASE_URL",
    "SETTING_SUSI_ACCOUNT_EMAIL",
    "SETTING_SUSI_ACCOUNT_NAME",
    "SETTING_SUSI_AUTH_TOKEN",
    "SETTING_SUSI_BASE_URL",
    "SUSI_BACKEND_ID",
    "SUSI_CREDENTIAL_KEYS",
    "clear_interpreter_credentials",
    "get_susi_auth_token",
    "get_susi_base_url",
    "get_susi_client",
    "is_interpreter_configured",
    "is_registered_interpreter",
    "is_susi_configured",
    "save_susi_credentials",
    "stop_interpreter_sessions",
    "strip_room_credential_keys",
    "susi_account_label",
    "susi_server_host",
]
