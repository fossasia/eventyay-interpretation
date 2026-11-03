from __future__ import annotations

import json
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


def _mark_connection_failure_flags(event_id: int) -> None:
    try:
        from .models import VoxbentoOAuthGrant

        grant = VoxbentoOAuthGrant.objects.filter(event_id=event_id).first()
        if grant:
            update_fields = []
            if not grant.event_provisioned:
                grant.event_provisioning_failed = True
                update_fields.append("event_provisioning_failed")
            if not grant.webhook_subscription_id:
                grant.webhook_subscription_failed = True
                update_fields.append("webhook_subscription_failed")
            if update_fields:
                grant.save(update_fields=update_fields)
    except Exception as err:
        logger.error(f"Failed to update grant failure flags for event {event_id}: {err}")


def _retry_connection(task, exc: Exception, event_id: int) -> None:
    """Retry task with backoff; on exhaustion mark failure flags and re-raise."""
    if task.request.retries >= task.max_retries:
        logger.error(f"Max retries exceeded syncing VoxBento connection for event {event_id}: {exc}")
        _mark_connection_failure_flags(event_id)
        raise exc
    try:
        task.retry(exc=exc)
    except (MaxRetriesExceededError, requests.RequestException, redis.exceptions.LockError):
        logger.error(f"Max retries exceeded syncing VoxBento connection for event {event_id}: {exc}")
        _mark_connection_failure_flags(event_id)
        raise


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_voxbento_connection(self, event_id: int) -> None:
    """
    Background task to sync the VoxBento OAuth connection.
    """
    try:
        event = Event._base_manager.get(pk=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant:
            return

        # Provision Event
        try:
            create_voxbento_event(event)
        except requests.RequestException as e:
            if (
                isinstance(e, requests.HTTPError)
                and e.response is not None
                and e.response.status_code < 500
                and e.response.status_code not in (408, 429)
            ):
                raise
            _retry_connection(self, e, event_id)

        # Reload grant to ensure we have latest flags
        grant.refresh_from_db()
        if not grant.event_provisioned or grant.event_provisioning_failed:
            logger.error(f"Event provisioning failed for event {event_id}. Halting task chain.")
            _mark_connection_failure_flags(event_id)
            return

        # Subscribe Webhooks
        try:
            subscribe_to_voxbento_webhooks(event)
        except requests.RequestException as e:
            if (
                isinstance(e, requests.HTTPError)
                and e.response is not None
                and e.response.status_code < 500
                and e.response.status_code not in (408, 429)
            ):
                logger.error(f"VoxBento webhook failed {e.response.status_code}: {e.response.text}")
                from .models import VoxbentoOAuthGrant

                VoxbentoOAuthGrant.objects.filter(id=grant.id, webhook_subscription_id__isnull=True).update(
                    webhook_subscription_failed=True
                )
            else:
                _retry_connection(self, e, event_id)

        # Reload grant to ensure we have latest webhook flags
        grant.refresh_from_db()
        if (not grant.webhook_subscription_id and not grant.webhook_scope_denied) or grant.webhook_subscription_failed:
            logger.error(f"Webhook subscription failed for event {event_id}. Halting task chain.")
            _mark_connection_failure_flags(event_id)
            return

        # Bulk Sync Rooms
        sync_all_rooms_to_voxbento.delay(event_id)

    except Event.DoesNotExist:
        logger.warning(f"Event {event_id} does not exist. Cannot sync VoxBento connection.")
        return
    except MaxRetriesExceededError:
        logger.error(f"Max retries exceeded syncing VoxBento connection for event {event_id}")
        _mark_connection_failure_flags(event_id)
    except (requests.RequestException, redis.exceptions.LockError) as e:
        logger.warning(f"Transient error syncing VoxBento connection for event {event_id}: {e}")
        _retry_connection(self, e, event_id)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_all_rooms_to_voxbento(self, event_id: int) -> None:
    try:
        event = Event._base_manager.get(pk=event_id)
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
        event = Event._base_manager.get(pk=event_id)
        grant = getattr(event, "voxbento_oauth_grant", None)
        if not grant or grant.event_provisioning_failed or grant.is_disconnected or grant.needs_reauth:
            return False

        if action == "delete":
            delete_voxbento_room(event, room_id)
            return False

        room = room_instance if room_instance else Room.objects.get(id=room_id)
        from .models import RoomInterpretation

        interpretation = RoomInterpretation.objects.filter(room_id=room.id).first()

        payload = {
            "name": str(room.name),
            "target_languages": [],
            "enable_transcription": getattr(interpretation, "enable_transcription", False) if interpretation else False,
            "transcription_provider": (getattr(interpretation, "transcription_provider", "") or None)
            if interpretation
            else None,
            "transcription_model": (getattr(interpretation, "transcription_model", "") or None)
            if interpretation
            else None,
            "source_language": (getattr(interpretation, "source_language", "") or None) if interpretation else None,
            "enable_translation": getattr(interpretation, "enable_translation", False) if interpretation else False,
            "translation_provider": (getattr(interpretation, "translation_provider", "") or None)
            if interpretation
            else None,
            "translation_model": (getattr(interpretation, "translation_model", "") or None) if interpretation else None,
        }

        from interpretation.backends.voxbento_api import get_voxbento_room_langs
        from interpretation.settings import use_plugin_language_streams

        use_plugin_streams = use_plugin_language_streams(event)
        new_lang_set = set()
        validated_langs = []
        if use_plugin_streams:
            if interpretation and interpretation.room_enabled and interpretation.interpreter == "voxbento":
                from .language_map import language_code_for_name
                from .utils import validate_target_language_codes

                # 1. Collect room-level target languages (user-configured)
                room_langs = set()
                if interpretation.language_streams:
                    for s in interpretation.language_streams:
                        if isinstance(s, dict):
                            lang_name = s.get("language")
                            if lang_name and lang_name != "Original":
                                code = language_code_for_name(lang_name)
                                if code and len(code) == 2 and code.isalpha():
                                    room_langs.add(code)
                elif interpretation.target_languages:
                    for code in interpretation.target_languages:
                        if isinstance(code, str):
                            c = code.strip().lower()
                            if len(c) == 2 and c.isalpha():
                                room_langs.add(c)

                new_lang_set.update(room_langs)

                # 2. Collect stream-schedule languages dynamically
                schedule_langs = set()
                if hasattr(room, "stream_schedules"):
                    for schedule in room.stream_schedules.all():
                        try:
                            cfg = schedule.config
                            if isinstance(cfg, str):
                                try:
                                    cfg = json.loads(cfg)
                                except Exception:
                                    cfg = {}
                            if not isinstance(cfg, dict):
                                continue
                            s_streams = cfg.get("language_streams") or cfg.get("languageUrls") or []
                            if isinstance(s_streams, list):
                                for s_entry in s_streams:
                                    if not isinstance(s_entry, dict):
                                        continue
                                    s_lang = s_entry.get("language")
                                    if s_lang and s_lang != "Original":
                                        code = language_code_for_name(s_lang)
                                        if code and len(code) == 2 and code.isalpha():
                                            schedule_langs.add(code)
                        except Exception as e:
                            logger.warning(
                                "Error reading schedule config for schedule %s: %s",
                                getattr(schedule, "id", None),
                                e,
                            )

                new_lang_set.update(schedule_langs)

                sorted_langs = sorted(new_lang_set)
                validated_langs = validate_target_language_codes(sorted_langs)
        else:
            # When not using plugin streams, we pull the target languages from the Eventyay room config
            # so VoxBento creates the necessary WHEP endpoint booths for them.
            new_lang_set = _extract_langs_from_module_config(room.module_config)
            validated_langs = list(new_lang_set)

        payload["target_languages"] = validated_langs

        response_data = sync_voxbento_room(event, room_id, payload)

        voxbento_room_id = response_data.get("room_id")
        if (
            voxbento_room_id
            and interpretation
            and interpretation.interpreter == RoomInterpretation.INTERPRETER_VOXBENTO
        ):
            from django.db import transaction

            with transaction.atomic():
                latest_interp = type(interpretation).objects.select_for_update().filter(pk=interpretation.pk).first()
                if latest_interp:
                    config = dict(latest_interp.backend_config) if latest_interp.backend_config else {}
                    config["voxbento_room_id"] = str(voxbento_room_id)
                    # Use update() to avoid triggering post_save signals which cause RecursionError
                    type(interpretation).objects.filter(pk=interpretation.pk).update(
                        backend_session_id=str(voxbento_room_id),
                        backend_config=config,
                    )

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
            base_url = get_voxbento_base_url(event).rstrip("/")
            returned_urls = {}
            booths_data = response_data["booths"]
            if isinstance(booths_data, dict):
                returned_urls = booths_data
            elif isinstance(booths_data, list):
                for b in booths_data:
                    # Note: Voxbento API does not include a 'type' field on booth objects;
                    # all booths in this response are human interpreter booths.
                    whep_url = b.get("whep_url")
                    if not whep_url:
                        whip_path = b.get("whip_path")
                        if whip_path:
                            whep_url = f"{base_url}/{whip_path}/whep"
                    if whep_url:
                        lang_key = b.get("language") or b.get("language_code")
                        if lang_key:
                            returned_urls[lang_key] = whep_url

            if not room_instance:
                room.refresh_from_db()

            if use_plugin_streams:
                interpretation = getattr(room, "interpretation", None)
                if interpretation:
                    from .language_map import language_code_for_name, language_name_for_code

                    new_streams = [dict(s) for s in (interpretation.language_streams or []) if isinstance(s, dict)]
                    needs_save = False
                    matched_codes = set()
                    room_target_codes = set(interpretation.target_languages or [])

                    for s_entry in new_streams:
                        s_lang = s_entry.get("language")
                        if not s_lang:
                            continue
                        s_code = language_code_for_name(s_lang)
                        if s_code in returned_urls:
                            matched_codes.add(s_code)
                            full_url = returned_urls[s_code]
                            existing_yt = s_entry.get("youtube_id") or ""
                            existing_u = s_entry.get("url") or ""
                            can_overwrite_u = not existing_u or "/whep" in existing_u or "localhost" in existing_u
                            can_overwrite_yt = not existing_yt or "/whep" in existing_yt or "localhost" in existing_yt
                            if can_overwrite_u and can_overwrite_yt:
                                if existing_yt != full_url or existing_u != full_url:
                                    s_entry["youtube_id"] = full_url
                                    if "url" in s_entry or not existing_yt:
                                        s_entry["url"] = full_url
                                    needs_save = True

                    for room_code in room_target_codes:
                        if room_code not in matched_codes and room_code in returned_urls:
                            full_url = returned_urls[room_code]
                            resolved_name = language_name_for_code(room_code)
                            new_streams.append(
                                {
                                    "language": resolved_name,
                                    "url": full_url,
                                    "youtube_id": full_url,
                                    "use_video": False,
                                }
                            )
                            needs_save = True

                    if needs_save:
                        interpretation.language_streams = new_streams
                        RoomInterpretation.objects.filter(pk=interpretation.pk).update(language_streams=new_streams)

                    # Also update stream schedules configured for this room with returned WHEP URLs
                    has_schedule_write_failure = False
                    try:
                        if hasattr(room, "stream_schedules"):
                            from django.db import transaction
                            from eventyay.base.models.stream_schedule import StreamSchedule

                            schedule_ids = list(room.stream_schedules.values_list("id", flat=True))
                            for sched_id in schedule_ids:
                                try:
                                    with transaction.atomic():
                                        schedule = (
                                            StreamSchedule.objects.select_for_update()
                                            .filter(id=sched_id, room_id=room.id)
                                            .first()
                                        )
                                        if not schedule:
                                            continue
                                        cfg = schedule.config
                                        if isinstance(cfg, str):
                                            try:
                                                cfg = json.loads(cfg)
                                            except Exception:
                                                cfg = {}
                                        if not isinstance(cfg, dict):
                                            continue
                                        s_config = dict(cfg)
                                        s_updated = False
                                        for key in ("language_streams", "languageUrls"):
                                            s_streams = s_config.get(key)
                                            if isinstance(s_streams, list) and s_streams:
                                                for s_entry in s_streams:
                                                    if not isinstance(s_entry, dict):
                                                        continue
                                                    s_lang = s_entry.get("language")
                                                    if s_lang:
                                                        s_code = language_code_for_name(s_lang)
                                                        if s_code in returned_urls:
                                                            s_url = returned_urls[s_code]
                                                            existing_u = s_entry.get("url") or ""
                                                            existing_yt = s_entry.get("youtube_id") or ""
                                                            can_overwrite_u = (
                                                                not existing_u
                                                                or "/whep" in existing_u
                                                                or "localhost" in existing_u
                                                            )
                                                            can_overwrite_yt = (
                                                                not existing_yt
                                                                or "/whep" in existing_yt
                                                                or "localhost" in existing_yt
                                                            )
                                                            if can_overwrite_u and can_overwrite_yt:
                                                                if existing_u != s_url or existing_yt != s_url:
                                                                    s_entry["url"] = s_url
                                                                    s_entry["youtube_id"] = s_url
                                                                    s_updated = True
                                                if s_updated:
                                                    s_config[key] = s_streams
                                        if s_updated:
                                            schedule.config = s_config
                                            schedule._skip_interpretation_sync = True
                                            schedule.save(update_fields=["config"])
                                except Exception as sched_err:
                                    logger.warning(
                                        "Failed to update schedule %s with WHEP URLs: %s",
                                        sched_id,
                                        sched_err,
                                    )
                                    has_schedule_write_failure = True
                    except Exception as e:
                        logger.warning("Failed to update stream schedules with VoxBento WHEP URLs: %s", e)
                        has_schedule_write_failure = True

                    if has_schedule_write_failure:
                        return True
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
                event = Event._base_manager.get(pk=event_id)
                if hasattr(event, "voxbento_oauth_grant"):
                    grant = event.voxbento_oauth_grant
                    grant.room_sync_failed = True
                    grant.save(update_fields=["room_sync_failed"])
            except Event.DoesNotExist:
                pass
