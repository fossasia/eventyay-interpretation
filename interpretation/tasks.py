import logging

import redis
import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from eventyay.base.models import Event, Room

from .backends.voxbento_api import (
    create_voxbento_event,
    delete_voxbento_room,
    subscribe_to_voxbento_webhooks,
    sync_voxbento_room,
)
from .room_control import serialize_room_interpretation

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_voxbento_connection(self, event_id: int) -> None:
    """
    Background task to sync the VoxBento OAuth connection.
    Chain: Create Event -> Subscribe Webhooks -> Sync Rooms.
    """
    try:
        event = Event.objects.get(id=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant:
            return

        # Step 1: Provision Event (Gated)
        try:
            create_voxbento_event(event)
        except requests.RequestException as e:
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code < 500:
                raise  # Don't retry 4xx errors for creation, it's failed.
            self.retry(exc=e)

        # Reload grant to ensure we have latest flags
        grant.refresh_from_db()
        if grant.event_provisioning_failed:
            logger.error(f"Event provisioning failed for event {event_id}. Halting task chain.")
            return

        # Step 2: Subscribe Webhooks
        try:
            subscribe_to_voxbento_webhooks(event)
        except requests.RequestException as e:
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code < 500:
                pass  # Already handled inside subscribe_to_voxbento_webhooks
            else:
                self.retry(exc=e)

        # Step 3: Bulk Sync Rooms (Independent of webhook success)
        sync_all_rooms_to_voxbento.delay(event_id)

    except Event.DoesNotExist:
        logger.warning(f"Event {event_id} does not exist. Cannot sync VoxBento connection.")
        return
    except (requests.RequestException, redis.exceptions.LockError) as e:
        logger.warning(f"Transient error syncing VoxBento connection for event {event_id}: {e}")
        try:
            self.retry(exc=e)
        except MaxRetriesExceededError:
            logger.error(f"Max retries exceeded syncing VoxBento connection for event {event_id}")


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_all_rooms_to_voxbento(self, event_id: int) -> None:
    try:
        event = Event.objects.get(id=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant or grant.event_provisioning_failed:
            return

        rooms = list(event.rooms.filter(deleted=False))
        for room in rooms:
            sync_single_room_to_voxbento.delay(room.id, event.id, "upsert")

    except Exception as e:
        logger.error(f"Failed to bulk sync rooms for event {event_id}: {e}", exc_info=True)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_single_room_to_voxbento(self, room_id: int, event_id: int, action: str) -> None:
    try:
        event = Event.objects.get(id=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant or grant.event_provisioning_failed:
            return

        if action == "delete":
            delete_voxbento_room(event, room_id)
            return

        room = Room.objects.get(id=room_id)
        # Upsert
        interpretation = getattr(room, "interpretation", None)
        data = serialize_room_interpretation(room, event, interpretation)

        # VoxBento payload expects specific fields, map them here:
        payload = {
            "name": str(room.name),
            "target_languages": [],
        }
        if interpretation and getattr(interpretation, "enabled", False):
            payload["target_languages"] = interpretation.languages
        sync_voxbento_room(event, room_id, payload)

    except (Event.DoesNotExist, Room.DoesNotExist):
        if action != "delete":
            logger.warning(f"Entity does not exist for upsert room {room_id}")
        return
    except requests.RequestException as e:
        try:
            self.retry(exc=e)
        except MaxRetriesExceededError:
            logger.error(f"Max retries exceeded syncing room {room_id}")
            if "event" in locals() and hasattr(event, "voxbento_oauth_grant"):
                grant = event.voxbento_oauth_grant
                grant.room_sync_failed = True
                grant.save(update_fields=["room_sync_failed"])
