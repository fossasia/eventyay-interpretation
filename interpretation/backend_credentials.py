"""Per-room interpreter credentials stored in ``RoomInterpretation.backend_config``."""

from __future__ import annotations

from urllib.parse import urlparse

from .models import RoomInterpretation
from .susi import SusiClient

SUSI_BASE_URL = "susi_base_url"
SUSI_AUTH_TOKEN = "susi_auth_token"
SUSI_ACCOUNT_EMAIL = "susi_account_email"
SUSI_ACCOUNT_NAME = "susi_account_name"

SUSI_CREDENTIAL_KEYS = frozenset(
    {
        SUSI_BASE_URL,
        SUSI_AUTH_TOKEN,
        SUSI_ACCOUNT_EMAIL,
        SUSI_ACCOUNT_NAME,
    }
)


def _config(interpretation: RoomInterpretation | None) -> dict:
    if interpretation is None:
        return {}
    return dict(interpretation.backend_config or {})


def get_susi_base_url(interpretation: RoomInterpretation | None) -> str:
    return (_config(interpretation).get(SUSI_BASE_URL) or "").strip().rstrip("/")


def get_susi_auth_token(interpretation: RoomInterpretation | None) -> str:
    return (_config(interpretation).get(SUSI_AUTH_TOKEN) or "").strip()


def get_susi_account_email(interpretation: RoomInterpretation | None) -> str:
    return (_config(interpretation).get(SUSI_ACCOUNT_EMAIL) or "").strip()


def get_susi_account_name(interpretation: RoomInterpretation | None) -> str:
    return (_config(interpretation).get(SUSI_ACCOUNT_NAME) or "").strip()


def is_susi_configured(interpretation: RoomInterpretation | None) -> bool:
    return bool(
        get_susi_base_url(interpretation) and get_susi_auth_token(interpretation)
    )


def save_susi_credentials(
    interpretation: RoomInterpretation,
    *,
    base_url: str,
    token: str,
    email: str = "",
    name: str = "",
) -> None:
    config = _config(interpretation)
    config[SUSI_BASE_URL] = (base_url or "").strip().rstrip("/")
    config[SUSI_AUTH_TOKEN] = (token or "").strip()
    config[SUSI_ACCOUNT_EMAIL] = (email or "").strip()
    config[SUSI_ACCOUNT_NAME] = (name or "").strip()
    interpretation.backend_config = config
    interpretation.save(update_fields=["backend_config"])


def clear_susi_credentials(interpretation: RoomInterpretation) -> None:
    config = _config(interpretation)
    for key in SUSI_CREDENTIAL_KEYS:
        config.pop(key, None)
    interpretation.backend_config = config
    interpretation.save(update_fields=["backend_config"])


def clear_backend_credentials(interpretation: RoomInterpretation) -> None:
    if interpretation.interpreter == RoomInterpretation.INTERPRETER_SUSI:
        clear_susi_credentials(interpretation)


def susi_account_label(interpretation: RoomInterpretation | None) -> str:
    name = get_susi_account_name(interpretation)
    email = get_susi_account_email(interpretation)
    if name and email:
        return f"{name} ({email})"
    return email or name


def susi_server_host(interpretation: RoomInterpretation | None) -> str:
    base_url = get_susi_base_url(interpretation)
    if not base_url:
        return ""
    return urlparse(base_url).netloc or base_url


def get_susi_client(interpretation: RoomInterpretation | None) -> SusiClient:
    return SusiClient(
        get_susi_base_url(interpretation),
        get_susi_auth_token(interpretation),
    )
