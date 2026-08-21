import logging
from urllib.parse import urljoin

import requests
from django.conf import settings
from eventyay.base.models import Event

from .voxbento_credentials import get_voxbento_base_url
from .voxbento_oauth import get_valid_access_token

logger = logging.getLogger(__name__)


def get_webhook_target_url() -> str:
    """Return the webhook receiver URL."""
    if settings.DEBUG:
        host = getattr(settings, "INTERPRETATION_WEBHOOK_PUBLIC_HOST", None)
        if host:
            return urljoin(host, "/interpretation/voxbento/webhook/")

    site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
    return urljoin(site_url, "/interpretation/voxbento/webhook/")


def subscribe_to_voxbento_webhooks(event: Event) -> None:
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

        base_url = get_voxbento_base_url(event)
        if not base_url:
            logger.warning("No base URL configured for VoxBento webhooks (Event %s)", event.id)
            return

        api_url = f"{base_url.rstrip('/')}/api/v1/webhooks"

        # Idempotency Guard: Tear down existing subscription if any
        if grant.webhook_subscription_id:
            try:
                access_token = get_valid_access_token(grant.id)
                headers = {"Authorization": f"Bearer {access_token}"}
                delete_url = f"{api_url}/{grant.webhook_subscription_id}"

                resp = requests.delete(delete_url, headers=headers, timeout=5.0)

                if resp.status_code == 404 or resp.status_code == 204:
                    # Treat 404 as successful no-op (already gone)
                    grant.webhook_subscription_id = None
                    grant.save(update_fields=["webhook_subscription_id"])
                else:
                    resp.raise_for_status()
                    # If it succeeded but returned something else (e.g. 200)
                    grant.webhook_subscription_id = None
                    grant.save(update_fields=["webhook_subscription_id"])
            except Exception as e:
                logger.error("Failed to delete existing VoxBento webhook %s: %s", grant.webhook_subscription_id, e)
                # Re-raise to let Celery retry if the network failed
                raise

        # Now, create the new subscription
        target_url = get_webhook_target_url()
        payload = {
            "target_url": target_url,
            "event_types": ["room.interpretation.started", "room.interpretation.stopped", "room.interpretation.failed"],
        }

        access_token = get_valid_access_token(grant.id)
        if not access_token:
            return

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
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
    payload = {
        "slug": event.slug,
        "name": str(event.name),
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


def sync_voxbento_room(event: Event, room_id: int, payload: dict) -> None:
    """
    Upserts the room in VoxBento via PUT /api/v1/events/{event_slug}/rooms/{room_id}
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return

    base_url = get_voxbento_base_url(event)
    if not base_url:
        return

    api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}/rooms/{room_id}"
    access_token = get_valid_access_token(grant.id)
    if not access_token:
        return

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.put(api_url, headers=headers, json=payload, timeout=5.0)
    resp.raise_for_status()


def delete_voxbento_room(event: Event, room_id: int) -> None:
    """
    Deletes the room in VoxBento via DELETE /api/v1/events/{event_slug}/rooms/{room_id}
    """
    grant = getattr(event, "voxbento_oauth_grant", None)
    if not grant:
        return

    base_url = get_voxbento_base_url(event)
    if not base_url:
        return

    api_url = f"{base_url.rstrip('/')}/api/v1/events/{event.slug}/rooms/{room_id}"
    access_token = get_valid_access_token(grant.id)
    if not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.delete(api_url, headers=headers, timeout=5.0)
    if resp.status_code != 404:
        resp.raise_for_status()
