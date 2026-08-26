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
from .backends.voxbento_credentials import get_voxbento_base_url
from .language_map import language_code_for_name

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_voxbento_connection(self, event_id: int) -> None:
    """
    Background task to sync the VoxBento OAuth connection.
    """
    try:
        event = Event.objects.get(id=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant:
            return

        # Provision Event
        try:
            create_voxbento_event(event)
        except requests.RequestException as e:
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code < 500:
                raise
            self.retry(exc=e)

        # Reload grant to ensure we have latest flags
        grant.refresh_from_db()
        if grant.event_provisioning_failed:
            logger.error(f"Event provisioning failed for event {event_id}. Halting task chain.")
            return

        # Subscribe Webhooks
        try:
            subscribe_to_voxbento_webhooks(event)
        except requests.RequestException as e:
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code < 500:
                logger.error(f"VoxBento webhook failed {e.response.status_code}: {e.response.text}")
                from .models import VoxbentoOAuthGrant

                VoxbentoOAuthGrant.objects.filter(id=grant.id, webhook_subscription_id__isnull=True).update(
                    webhook_subscription_failed=True
                )
            else:
                self.retry(exc=e)

        # Bulk Sync Rooms
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


class ActiveSessionConflict(Exception):
    pass


def _extract_langs_from_module_config(module_config) -> set:
    """Extract language codes from an Eventyay Room module_config list."""
    langs = set()
    if not module_config:
        return langs
    for module in module_config:
        if module.get("type") in ("livestream.youtube", "livestream.native"):
            for lang_entry in module.get("config", {}).get("languageUrls", []):
                lang_name = lang_entry.get("language")
                if lang_name:
                    langs.add(language_code_for_name(lang_name))
    return langs


def _do_sync_single_room_to_voxbento(
    room_id: int, event_id: int, action: str, room_instance=None, old_module_config=None
) -> bool:
    try:
        event = Event.objects.get(id=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if (
            not grant
            or grant.event_provisioning_failed
            or grant.is_disconnected
            or grant.needs_reauth
            or grant.webhook_scope_denied
        ):
            return False

        if action == "delete":
            delete_voxbento_room(event, room_id)
            return False

        room = room_instance if room_instance else Room.objects.get(id=room_id)

        payload = {
            "name": str(room.name),
            "target_languages": [],
        }

        from interpretation.backends.voxbento_api import get_voxbento_room_langs
        from interpretation.settings import use_plugin_language_streams

        use_plugin_streams = use_plugin_language_streams(event)
        if use_plugin_streams:
            interpretation = getattr(room, "interpretation", None)
            if interpretation and interpretation.target_languages:
                new_lang_set = set(interpretation.target_languages)
            else:
                new_lang_set = set()
        else:
            new_lang_set = _extract_langs_from_module_config(room.module_config)

        payload["target_languages"] = list(new_lang_set)

        response_data = sync_voxbento_room(event, room_id, payload)

        if response_data.get("error") == 409:
            # We got a 409, meaning an active session was found.
            # To be certain we actually attempted to remove a language, we fetch the
            # remote state of VoxBento and compare it with what we just sent.
            old_lang_set = get_voxbento_room_langs(event, room_id)
            langs_being_removed = old_lang_set - new_lang_set

            if langs_being_removed:
                # A language with an active session is being deleted — block the save.
                logger.error("VoxBento refused to sync room %s due to active session. Aborting.", room_id)
                grant.room_sync_failed = True
                grant.save(update_fields=["room_sync_failed"])
                detail = response_data.get("detail", "Cannot remove language while it has an active session running.")
                raise ActiveSessionConflict(detail)
            else:
                # 409 with no language deletions = stale VoxBento registry state. Safe to ignore.
                logger.warning(
                    "VoxBento returned 409 for room %s but no languages are being removed. Ignoring stale state.",
                    room_id,
                )
                return False

        # On success, clear the sync failure flag if it was previously set
        if grant.room_sync_failed:
            grant.room_sync_failed = False
            grant.save(update_fields=["room_sync_failed"])

        if response_data and "booths" in response_data:
            returned_urls = {
                b["language"]: b.get("whep_url", f"{get_voxbento_base_url(event).rstrip('/')}/{b['whip_path']}/whep")
                for b in response_data["booths"]
            }

            if not room_instance:
                room.refresh_from_db()

            if use_plugin_streams:
                interpretation = getattr(room, "interpretation", None)
                if interpretation:
                    # Update language_streams
                    new_streams = list(interpretation.language_streams) if interpretation.language_streams else []

                    # Convert to a dict to update/merge urls
                    stream_dict = {entry["language"]: entry for entry in new_streams if "language" in entry}
                    needs_save = False

                    for lang, full_url in returned_urls.items():
                        if lang in stream_dict:
                            existing = stream_dict[lang].get("youtube_id") or ""
                            if not existing or "/whep" in existing or "localhost" in existing:
                                if existing != full_url:
                                    stream_dict[lang]["youtube_id"] = full_url
                                    needs_save = True
                        else:
                            # Language added but wasn't in streams
                            stream_dict[lang] = {"language": lang, "youtube_id": full_url}
                            needs_save = True

                    if needs_save:
                        interpretation.language_streams = list(stream_dict.values())
                        interpretation.save(update_fields=["language_streams"])
            else:
                needs_save = False
                if room.module_config:
                    for module in room.module_config:
                        if module.get("type") in ("livestream.youtube", "livestream.native"):
                            config = module.get("config", {})
                            languages = config.get("languageUrls", [])
                            for lang_entry in languages:
                                lang_name = lang_entry.get("language")
                                if lang_name:
                                    lang_code = language_code_for_name(lang_name)
                                    if lang_code in returned_urls:
                                        full_url = returned_urls[lang_code]
                                        existing = lang_entry.get("youtube_id") or ""
                                        if not existing or "/whep" in existing or "localhost" in existing:
                                            if existing != full_url:
                                                lang_entry["youtube_id"] = full_url
                                                needs_save = True

                if needs_save:
                    # Use update_fields to avoid re-triggering our pre_save signal.
                    Room.objects.filter(id=room.id).update(module_config=room.module_config)

        return False
    except (Event.DoesNotExist, Room.DoesNotExist):
        if action != "delete":
            logger.warning(f"Entity does not exist for upsert room {room_id}")
        return False
    except requests.RequestException as e:
        logger.warning(f"Request exception when syncing room {room_id}: {e}")
        return True


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_single_room_to_voxbento(self, room_id: int, event_id: int, action: str) -> None:
    try:
        needs_retry = _do_sync_single_room_to_voxbento(room_id, event_id, action)
    except ActiveSessionConflict:
        return
    if needs_retry:
        try:
            self.retry()
        except MaxRetriesExceededError:
            logger.error(f"Max retries exceeded syncing room {room_id}")
            try:
                event = Event.objects.get(id=event_id)
                if hasattr(event, "voxbento_oauth_grant"):
                    grant = event.voxbento_oauth_grant
                    grant.room_sync_failed = True
                    grant.save(update_fields=["room_sync_failed"])
            except Event.DoesNotExist:
                pass
