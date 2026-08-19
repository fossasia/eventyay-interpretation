from __future__ import annotations

from urllib.parse import urlparse

import requests
from eventyay.base.models import Event

SETTING_VOXBENTO_BASE_URL = "interpretation_voxbento_base_url"
SETTING_VOXBENTO_API_KEY = "interpretation_voxbento_api_key"

EVENT_SETTINGS_KEYS = frozenset(
    {
        SETTING_VOXBENTO_BASE_URL,
        SETTING_VOXBENTO_API_KEY,
    }
)


class VoxbentoError(Exception):
    """Exception raised for VoxBento API errors."""

    pass


def get_voxbento_base_url(event: Event) -> str:
    from eventyay.base.settings import GlobalSettingsObject
    
    # Check Global Settings first
    gs = GlobalSettingsObject().settings
    global_url = gs.get("voxbento_base_url", "")
    
    # Fallback to legacy event-level settings
    url = global_url or event.settings.get(
        SETTING_VOXBENTO_BASE_URL, default="", as_type=str
    ).strip()
    
    if not url:
        return ""
        
    parsed = urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise VoxbentoError("VoxBento Base URL must use HTTPS unless running locally.")
        
    return url


def get_voxbento_api_key(event: Event) -> str:
    return event.settings.get(SETTING_VOXBENTO_API_KEY, default="", as_type=str).strip()


def is_voxbento_configured(event: Event | None) -> bool:
    if event is None:
        return False
    from ..models import VoxbentoOAuthGrant
    has_oauth = VoxbentoOAuthGrant.objects.filter(event=event).exists()
    has_legacy = bool(get_voxbento_api_key(event))
    return bool(get_voxbento_base_url(event) and (has_oauth or has_legacy))


def save_voxbento_credentials(event: Event, base_url: str, api_key: str) -> None:
    event.settings.set(SETTING_VOXBENTO_BASE_URL, base_url.strip().rstrip("/"))
    event.settings.set(SETTING_VOXBENTO_API_KEY, api_key.strip())


def clear_voxbento_credentials(event: Event) -> None:
    for key in EVENT_SETTINGS_KEYS:
        event.settings.delete(key)


def voxbento_server_host(event: Event) -> str:
    url = get_voxbento_base_url(event)
    if not url:
        return ""
    return urlparse(url).netloc


def test_voxbento_connection(base_url: str, api_key: str, event_slug: str) -> None:
    """Tests the VoxBento connection by attempting to generate a listener token."""
    url = f"{base_url.rstrip('/')}/api/v1/tokens/listener"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"event_slug": event_slug}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=5.0)
    except requests.RequestException as e:
        raise VoxbentoError(f"Connection failed: {e}")

    if not response.ok:
        try:
            error_msg = response.json().get("detail", response.text)
        except ValueError:
            error_msg = response.text
        raise VoxbentoError(f"VoxBento API error ({response.status_code}): {error_msg}")
