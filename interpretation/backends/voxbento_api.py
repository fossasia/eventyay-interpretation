import logging
import os
import socket
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings
from eventyay.base.models import Event

from .voxbento_credentials import get_voxbento_base_url
from .voxbento_oauth import VoxbentoReauthorizationRequired, get_valid_access_token

logger = logging.getLogger(__name__)


def get_webhook_target_url() -> str:
    """Return the webhook receiver URL."""
    host = getattr(settings, "INTERPRETATION_WEBHOOK_PUBLIC_HOST", None) or os.environ.get(
        "INTERPRETATION_WEBHOOK_PUBLIC_HOST"
    )
    if host:
        return urljoin(host, "/interpretation/voxbento/webhook/")

    site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
    if "localhost" in site_url or "127.0.0.1" in site_url:
        try:
            socket.gethostbyname("host.docker.internal")
            parsed_site = urlparse(site_url)
            port_part = f":{parsed_site.port}" if parsed_site.port else ""
            scheme = parsed_site.scheme or "http"
            return urljoin(f"{scheme}://host.docker.internal{port_part}", "/interpretation/voxbento/webhook/")
        except Exception:
            pass

    return urljoin(site_url, "/interpretation/voxbento/webhook/")


def subscribe_to_voxbento_webhooks(event: Event, force: bool = False) -> None:
    """
    Subscribes Eventyay to VoxBento webhooks.
    Includes idempotency guards, DB concurrency locking, and failure reporting.
    """
    from django.db import transaction

    with transaction.atomic():
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant:
            return

        # Lock the row for update to prevent concurrent subscriptions
        grant = grant.__class__.objects.select_for_update().get(pk=grant.pk)

        # Skip if webhook subscription is already healthy and active
        if grant.webhook_subscription_id and not grant.webhook_subscription_failed and not force:
            logger.info(
                "Webhook subscription %s already active for event %s. Skipping.",
                grant.webhook_subscription_id,
                event.id,
            )
            return

        base_url = get_voxbento_base_url(event)
        if not base_url:
            logger.warning("No base URL configured for VoxBento webhooks (Event %s)", event.id)
            return

        api_url = f"{base_url.rstrip('/')}/api/v1/webhooks"

        access_token = get_valid_access_token(grant.id)
        if not access_token:
            return

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        old_subscription_id = grant.webhook_subscription_id

        # Create the new subscription first without removing the working target
        target_url = get_webhook_target_url()
        payload = {
            "target_url": target_url,
            "event_types": ["booth.transcription.started", "booth.transcription.stopped", "booth.interpreter.joined"],
        }

        resp = requests.post(api_url, headers=headers, json=payload, timeout=5.0)

        if resp.status_code == 403:
            # Scope denied by VoxBento
            logger.error("VoxBento returned 403 Forbidden. Webhook scope denied for event %s", event.id)
            grant.webhook_scope_denied = True
            grant.save(update_fields=["webhook_scope_denied"])
            return

        if resp.status_code == 409:
            # Conflict / State drift
            logger.error("VoxBento returned 409 Conflict for webhooks on event %s. State drift detected.", event.id)
            grant.webhook_subscription_failed = True
            grant.save(update_fields=["webhook_subscription_failed"])
            return

        resp.raise_for_status()

        data = resp.json()

        # Save new subscription details
        grant.webhook_subscription_id = data["id"]
        grant.webhook_secret_key = data["secret_key"]
        grant.webhook_scope_denied = False
        grant.webhook_subscription_failed = False
        grant.save(
            update_fields=[
                "webhook_subscription_id",
                "webhook_secret_key",
                "webhook_scope_denied",
                "webhook_subscription_failed",
            ]
        )

        # Delete superseded old subscription if replaced with a different ID
        if old_subscription_id and str(old_subscription_id) != str(data["id"]):
            try:
                delete_url = f"{api_url}/{old_subscription_id}"
                del_headers = {"Authorization": f"Bearer {access_token}"}
                requests.delete(delete_url, headers=del_headers, timeout=5.0)
            except Exception as e:
                logger.warning("Failed to delete superseded VoxBento webhook %s: %s", old_subscription_id, e)


def create_voxbento_event(event: Event) -> None:
    """
    Auto-provisions the event in VoxBento via POST /api/v1/events/
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return

    base_url = get_voxbento_base_url(event)
    if not base_url:
        logger.warning("No base URL configured for VoxBento (Event %s)", event.id)
        return

    api_url = f"{base_url.rstrip('/')}/api/v1/events/"
    access_token = get_valid_access_token(grant.id)
    if not access_token:
        return

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    organizer_emails = set()
    if hasattr(event, "organizer") and event.organizer:
        from django.db.models import Q

        teams = (
            event.organizer.teams.filter(can_change_event_settings=True)
            .filter(Q(all_events=True) | Q(limit_events=event))
            .distinct()
        )
        for team in teams:
            for u in team.members.filter(is_active=True):
                if u.email:
                    organizer_emails.add(u.email.lower().strip())

    payload = {
        "slug": event.slug,
        "name": str(event.name),
        "organizer_emails": list(organizer_emails),
    }

    resp = requests.post(api_url, headers=headers, json=payload, timeout=5.0)

    if resp.status_code == 409:
        # Event already exists (idempotent success)
        logger.info("VoxBento event %s already exists.", event.slug)
        grant.event_provisioned = True
        grant.event_provisioning_failed = False
        grant.save(update_fields=["event_provisioned", "event_provisioning_failed"])
        return

    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to provision VoxBento event %s: %s", event.id, e)
        if resp.status_code < 500 and resp.status_code not in (408, 429):
            grant.event_provisioning_failed = True
            grant.save(update_fields=["event_provisioning_failed"])
        raise

    grant.event_provisioned = True
    grant.event_provisioning_failed = False
    grant.save(update_fields=["event_provisioned", "event_provisioning_failed"])


def delete_voxbento_event(event: Event) -> None:
    """
    Permanently wipes the event from VoxBento via DELETE /api/v1/events/{slug}
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return

    base_url = get_voxbento_base_url(event)
    if not base_url:
        return

    api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}"
    access_token = get_valid_access_token(grant.id)
    if not access_token:
        raise ValueError("Cannot delete event: Requires an active OAuth connection.")

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    resp = requests.delete(api_url, headers=headers, timeout=5.0)

    if resp.status_code == 404:
        return

    if resp.status_code == 409:
        raise ValueError("Cannot delete event: Active sessions are running.")

    resp.raise_for_status()


def sync_voxbento_room(event: Event, room_id: int, payload: dict) -> dict:
    """
    Upserts the room in VoxBento via PUT /api/v1/events/{event_slug}/rooms/{room_id}
    Returns the JSON response from VoxBento on success.
    Raises RequestException on HTTP errors (except 409).
    If 409 is returned (e.g. active session blocking sync), returns {"error": 409}.
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return {}

    base_url = get_voxbento_base_url(event)
    if not base_url:
        return {}

    api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}/rooms/{room_id}"
    access_token = get_valid_access_token(grant.id)
    if not access_token:
        return {}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.put(api_url, headers=headers, json=payload, timeout=5.0)

    if resp.status_code == 404:
        logger.warning(
            "VoxBento returned 404 Not Found for room sync on event %s. "
            "It may have been deleted. Attempting re-provision.",
            event.id,
        )
        create_voxbento_event(event)
        # Retry the request
        logger.debug("VOXBENTO_PAYLOAD_DEBUG: %s %s", api_url, payload)
        resp = requests.put(api_url, headers=headers, json=payload, timeout=5.0)

    if resp.status_code == 409:
        logger.error("VoxBento returned 409 Conflict for room sync on event %s room %s", event.id, room_id)
        try:
            detail = resp.json().get("detail", "Active session conflict")
        except Exception:
            detail = resp.text or "Active session conflict"
        return {"error": 409, "detail": detail}

    resp.raise_for_status()
    return resp.json()


def sync_voxbento_api_keys(event: Event) -> None:
    """
    Syncs the event's API keys to VoxBento via PATCH /api/v1/events/{event_slug}/api_keys.
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return

    base_url = get_voxbento_base_url(event)
    if not base_url:
        return

    api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}/api_keys"
    try:
        access_token = get_valid_access_token(grant.id)
        if not access_token:
            raise ValueError("Cannot synchronize API keys: Requires an active OAuth connection.")
    except VoxbentoReauthorizationRequired:
        raise
    except requests.RequestException as e:
        raise ValueError(f"Cannot synchronize API keys: Network error ({e})")

    payload = {
        "openai_api_key": grant.openai_api_key,
        "deepgram_api_key": grant.deepgram_api_key,
        "nvidia_api_key": grant.nvidia_api_key,
        "elevenlabs_api_key": grant.elevenlabs_api_key,
        "translation_openai_api_key": grant.translation_openai_api_key,
        "openrouter_api_key": grant.openrouter_api_key,
        "gemini_api_key": grant.gemini_api_key,
        "anthropic_api_key": grant.anthropic_api_key,
        "groq_api_key": grant.groq_api_key,
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.patch(api_url, headers=headers, json=payload, timeout=5.0)

        if resp.status_code == 404:
            logger.warning(
                "VoxBento returned 404 Not Found for API keys sync on event %s. Attempting to provision event.",
                event.id,
            )
            create_voxbento_event(event)
            resp = requests.patch(api_url, headers=headers, json=payload, timeout=5.0)

        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to sync API keys to VoxBento for event %s: %s", event.id, e)
        raise ValueError(f"Failed to synchronize API keys with VoxBento: {e}")


def delete_voxbento_room(event: Event, room_id: int) -> None:
    """
    Deletes the room in VoxBento via DELETE /api/v1/events/{event_slug}/rooms/{room_id}
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return

    base_url = get_voxbento_base_url(event)
    if not base_url:
        raise requests.RequestException("Cannot delete VoxBento room: no base URL configured.")

    api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}/rooms/{room_id}"
    access_token = get_valid_access_token(grant.id)
    if not access_token:
        raise requests.RequestException("Cannot delete VoxBento room: no access token available.")

    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.delete(api_url, headers=headers, timeout=5.0)
    if resp.status_code != 404:
        resp.raise_for_status()


def get_voxbento_room_langs(event: Event, room_id: int) -> set[str]:
    """
    Returns the set of language codes currently registered in VoxBento for this room.
    Used to detect which languages are being removed during a sync, so we can
    correctly scope the ActiveSessionConflict guard only to actual deletions.
    Returns an empty set on any error (fail-open: no false positives).
    """
    try:
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant:
            return set()

        base_url = get_voxbento_base_url(event)
        if not base_url:
            return set()

        api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}/rooms/{room_id}/booths"
        access_token = get_valid_access_token(grant.id)
        if not access_token:
            return set()

        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(api_url, headers=headers, timeout=5.0)
        if resp.status_code != 200:
            return set()

        booths_data = resp.json()
        return {b["language_code"] for b in booths_data if b.get("language_code")}
    except Exception:
        return set()
