from __future__ import annotations

from urllib.parse import urlparse

import requests
from eventyay.base.models import Event

from interpretation.operational_log import logged_request

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
    url = global_url or event.settings.get(SETTING_VOXBENTO_BASE_URL, default="", as_type=str).strip()

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
    try:
        base_url = get_voxbento_base_url(event)
    except VoxbentoError:
        base_url = ""
    for key in EVENT_SETTINGS_KEYS:
        event.settings.delete(key)

    from ..models import VoxbentoOAuthGrant

    try:
        grant = VoxbentoOAuthGrant.objects.get(event=event)
    except VoxbentoOAuthGrant.DoesNotExist:
        grant = None

    if grant:
        import logging

        from .voxbento_oauth import _get_cache_lock

        logger = logging.getLogger(__name__)

        lock_key = f"voxbento:refresh:{grant.id}"
        import redis.exceptions

        try:
            with _get_cache_lock(lock_key, timeout=22, blocking_timeout=24):
                grant.refresh_from_db()

                # Best effort revoke and webhook delete
                if base_url:
                    try:
                        # Attempt remote webhook deletion
                        if grant.webhook_subscription_id:
                            api_url = f"{base_url.rstrip('/')}/api/v1/webhooks"
                            access_token = grant.access_token
                            if access_token:
                                headers = {"Authorization": f"Bearer {access_token}"}
                                delete_url = f"{api_url}/{grant.webhook_subscription_id}"
                                resp = logged_request(
                                    "interpretation",
                                    "DELETE",
                                    delete_url,
                                    headers=headers,
                                    timeout=(3.0, 5.0),
                                    ok_statuses=(404,),
                                )
                                if resp.status_code not in (204, 404):
                                    resp.raise_for_status()
                    except Exception as e:
                        logger.error(
                            "Failed to delete VoxBento webhook subscription %s for event %s: %s",
                            grant.webhook_subscription_id,
                            event.id,
                            str(e),
                        )

                    try:
                        # Attempt remote oauth revocation
                        from eventyay.base.settings import GlobalSettingsObject

                        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
                        client_secret = GlobalSettingsObject().settings.get("voxbento_client_secret", "")
                        if client_id and grant.access_token:
                            revoke_url = f"{base_url.rstrip('/')}/oauth/revoke"
                            resp = logged_request(
                                "interpretation",
                                "POST",
                                revoke_url,
                                data={
                                    "token": grant.access_token,
                                    "client_id": client_id,
                                    "client_secret": client_secret,
                                },
                                timeout=(3.0, 5.0),
                                allow_redirects=False,
                            )
                            if not (200 <= resp.status_code < 300):
                                logger.warning(
                                    "OAuth revoke returned unexpected status %s for event %s",
                                    resp.status_code,
                                    event.id,
                                )
                    except Exception as e:
                        logger.error(
                            "Failed to revoke VoxBento OAuth token for event %s: %s",
                            event.id,
                            str(e),
                            extra={"notify": True},
                        )

                grant.access_token = ""
                grant.refresh_token = ""
                grant.is_disconnected = True
                grant.save(update_fields=["access_token", "refresh_token", "is_disconnected"])
        except redis.exceptions.LockError:
            raise VoxbentoError(
                "The integration is currently syncing. Please try disconnecting again in a few seconds."
            )


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
        response = logged_request("interpretation", "POST", url, headers=headers, json=payload, timeout=5.0)
    except requests.RequestException as e:
        raise VoxbentoError(f"Connection failed: {e}")

    if not response.ok:
        try:
            error_msg = response.json().get("detail", response.text)
        except ValueError:
            error_msg = response.text
        raise VoxbentoError(f"VoxBento API error ({response.status_code}): {error_msg}")
