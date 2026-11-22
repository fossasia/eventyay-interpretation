import logging
from datetime import timedelta

import redis.exceptions
import requests
from django.core.cache import cache
from django.utils import timezone

from interpretation.operational_log import logged_request

from ..models import VoxbentoOAuthGrant
from .voxbento_credentials import get_voxbento_base_url

logger = logging.getLogger(__name__)


class VoxbentoTemporarilyUnavailable(requests.RequestException):
    """Raised when VoxBento API or locking is temporarily unavailable."""

    pass


class VoxbentoReauthorizationRequired(requests.RequestException):
    """Raised when the OAuth token is invalid/revoked and user must re-authenticate."""

    pass


def call_voxbento_refresh(refresh_token, base_url):
    url = f"{base_url.rstrip('/')}/oauth/token"
    from eventyay.base.settings import GlobalSettingsObject

    gs = GlobalSettingsObject().settings
    client_id = gs.get("voxbento_client_id", "")
    client_secret = gs.get("voxbento_client_secret", "")

    try:
        response = logged_request(
            "interpretation",
            "POST",
            url,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
            timeout=5.0,
        )
    except requests.RequestException as e:
        logger.warning(f"Network error refreshing VoxBento token: {e}")
        raise VoxbentoTemporarilyUnavailable("Network error connecting to VoxBento")

    if response.status_code in (400, 401):
        raise VoxbentoReauthorizationRequired("VoxBento rejected the refresh token")

    if not response.ok:
        logger.warning(f"VoxBento returned {response.status_code} during token refresh")
        raise VoxbentoTemporarilyUnavailable(f"VoxBento API returned {response.status_code}")

    data = response.json()
    if "access_token" not in data or "refresh_token" not in data:
        raise VoxbentoTemporarilyUnavailable("VoxBento returned invalid token payload")

    return data


import contextlib


@contextlib.contextmanager
def _get_cache_lock(lock_key, timeout=10, blocking_timeout=8):
    if hasattr(cache, "lock"):
        with cache.lock(lock_key, timeout=timeout, blocking_timeout=blocking_timeout):
            yield
    else:
        yield


def get_valid_access_token(grant_id):
    grant = VoxbentoOAuthGrant.objects.get(id=grant_id)

    # Expiry Buffer: Refresh if less than 60s remaining
    if grant.expires_at and grant.expires_at > timezone.now() + timedelta(seconds=60):
        return grant.access_token

    lock_key = f"voxbento:refresh:{grant_id}"
    try:
        with _get_cache_lock(lock_key):
            grant.refresh_from_db()

            # Re-check after acquiring lock
            if grant.is_disconnected:
                raise VoxbentoReauthorizationRequired("Integration disconnected during refresh")

            if grant.expires_at and grant.expires_at > timezone.now() + timedelta(seconds=60):
                return grant.access_token

            base_url = get_voxbento_base_url(grant.event)
            if not base_url:
                raise VoxbentoReauthorizationRequired("VoxBento base URL is not configured")

            initial_refresh_token = grant.refresh_token
            initial_access_token = grant.access_token
            try:
                new_tokens = call_voxbento_refresh(grant.refresh_token, base_url)

                # Atomic token update to prevent re-connecting a disconnected grant
                expires_in = new_tokens.get("expires_in", 3600)
                expires_at = timezone.now() + timedelta(seconds=expires_in)

                from django.db import transaction

                with transaction.atomic():
                    current_grant = type(grant).objects.select_for_update().filter(id=grant.id).first()
                    if not current_grant or current_grant.is_disconnected:
                        updated = 0
                        reauth_msg = "Integration disconnected during refresh network call"
                    elif (
                        current_grant.refresh_token != initial_refresh_token
                        or current_grant.access_token != initial_access_token
                    ):
                        # Another refresh or OAuth reauthorization updated credentials concurrently
                        updated = 0
                        reauth_msg = "Integration reconnected concurrently"
                    else:
                        current_grant.access_token = new_tokens["access_token"]
                        current_grant.refresh_token = new_tokens["refresh_token"]
                        current_grant.expires_at = expires_at
                        current_grant.needs_reauth = False
                        current_grant.save(
                            update_fields=["access_token", "refresh_token", "expires_at", "needs_reauth"]
                        )
                        updated = 1

                if updated == 0:
                    if current_grant and current_grant.is_disconnected:
                        # Best-effort revoke the newly issued token since the integration was disconnected
                        try:
                            from eventyay.base.settings import GlobalSettingsObject

                            client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
                            client_secret = GlobalSettingsObject().settings.get("voxbento_client_secret", "")
                            if client_id:
                                logged_request(
                                    "interpretation",
                                    "POST",
                                    f"{base_url.rstrip('/')}/oauth/revoke",
                                    data={
                                        "token": new_tokens["access_token"],
                                        "client_id": client_id,
                                        "client_secret": client_secret,
                                    },
                                    timeout=(3.0, 5.0),
                                )
                        except Exception:
                            pass
                        raise VoxbentoReauthorizationRequired(reauth_msg)

                    # Re-check if grant was concurrently updated with valid tokens
                    latest_grant = type(grant).objects.filter(id=grant.id).first()
                    if (
                        latest_grant
                        and not latest_grant.is_disconnected
                        and latest_grant.expires_at
                        and latest_grant.expires_at > timezone.now() + timedelta(seconds=60)
                    ):
                        return latest_grant.access_token

                    raise VoxbentoTemporarilyUnavailable(reauth_msg)

                grant.access_token = new_tokens["access_token"]
                grant.refresh_token = new_tokens["refresh_token"]
                grant.expires_at = expires_at
                grant.needs_reauth = False

                return grant.access_token

            except VoxbentoReauthorizationRequired:
                grant.needs_reauth = True
                grant.save(update_fields=["needs_reauth"])
                raise

    except redis.exceptions.LockError:
        # Lock acquisition failed (timeout). Do not flag as needing reauth.
        logger.warning(f"Could not acquire lock to refresh VoxBento token for grant_id={grant_id}")
        raise VoxbentoTemporarilyUnavailable("Lock acquisition timed out during refresh")
