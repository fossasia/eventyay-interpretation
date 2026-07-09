"""Shared per-room interpretation helpers for commons views and video admin API."""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .models import RoomInterpretation
from .services import start_stream_session
from .settings import get_susi_client, is_susi_configured, is_susi_connected, is_interpretation_enabled
from .susi import SusiError
from .utils import (
    build_room_captions_url,
    clear_module_interpretation,
    get_room_stream_url,
    interpretation_dashboard_url,
    normalize_target_languages,
    set_module_interpretation,
)

PLUGIN_MODULE = "interpretation"


def plugin_enabled(event) -> bool:
    return PLUGIN_MODULE in event.get_plugins()


def get_interpretation(room) -> RoomInterpretation | None:
    return RoomInterpretation.objects.filter(room=room).first()


def normalize_session_status(status: str) -> str:
    """Map legacy stopped/error rows to idle for display and API."""
    if status == RoomInterpretation.STATUS_RUNNING:
        return RoomInterpretation.STATUS_RUNNING
    return RoomInterpretation.STATUS_IDLE


def attendee_interpretation_payload(
    interpretation: RoomInterpretation, *, captions_url: str = ""
) -> dict | None:
    """Build stream-module interpretation config pushed to attendees."""
    if not interpretation.room_enabled:
        return None
    running = (
        interpretation.status == RoomInterpretation.STATUS_RUNNING
        and bool((captions_url or "").strip())
    )
    return {
        "room_enabled": True,
        "enabled": running,
        "languages": list(interpretation.target_languages or []),
        "url": captions_url.strip() if running else "",
    }


def serialize_room_interpretation(room, event, interpretation=None) -> dict:
    if interpretation is None:
        interpretation = get_interpretation(room)
    detected_stream_url = get_room_stream_url(room)
    stream_url = ""
    if interpretation and interpretation.stream_url:
        stream_url = interpretation.stream_url
    return {
        "target_languages": list(interpretation.target_languages or [])
        if interpretation
        else [],
        "transcription_provider": interpretation.transcription_provider
        if interpretation
        else "",
        "translation_provider": interpretation.translation_provider
        if interpretation
        else "",
        "room_enabled": bool(interpretation.room_enabled) if interpretation else False,
        "status": normalize_session_status(
            interpretation.status if interpretation else RoomInterpretation.STATUS_IDLE
        ),
        "session_id": interpretation.susi_session_id if interpretation else "",
        "stream_url": stream_url or detected_stream_url,
        "detected_stream_url": detected_stream_url,
        "plugin_enabled": plugin_enabled(event),
        "susi_connected": is_susi_connected(event),
        "interpretation_enabled": is_interpretation_enabled(event),
        "interpretation_ready": is_susi_configured(event),
        "dashboard_url": interpretation_dashboard_url(
            event.organizer.slug, event.slug
        ),
    }


def update_room_interpretation(room, event, data: dict) -> RoomInterpretation:
    if not is_susi_connected(event):
        raise ValueError(_("Connect to SUSI on the interpretation dashboard first."))

    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)
    if "target_languages" in data:
        interpretation.target_languages = normalize_target_languages(
            data.get("target_languages")
        )
    if "transcription_provider" in data:
        interpretation.transcription_provider = (
            data.get("transcription_provider") or ""
        ).strip()
    if "translation_provider" in data:
        interpretation.translation_provider = (
            data.get("translation_provider") or ""
        ).strip()
    if "room_enabled" in data:
        interpretation.room_enabled = bool(data.get("room_enabled"))
    interpretation.save()

    if "room_enabled" in data and not interpretation.room_enabled:
        if interpretation.status == RoomInterpretation.STATUS_RUNNING:
            stop_room_session(room, event)
            return get_interpretation(room) or interpretation
        sync_attendee_interpretation(room, interpretation, event)
        return interpretation

    if interpretation.room_enabled:
        captions_url = ""
        if interpretation.status == RoomInterpretation.STATUS_RUNNING:
            captions_url = build_room_captions_url(event, room.pk, request=None)
        sync_attendee_interpretation(room, interpretation, event, captions_url=captions_url)
    return interpretation


@dataclass
class SessionResult:
    ok: bool
    error: str = ""
    interpretation: RoomInterpretation | None = None


def _notify_room_config_changed(event) -> None:
    from asgiref.sync import async_to_sync
    from eventyay.base.services.event import notify_event_change

    async_to_sync(notify_event_change)(event.id)


def sync_attendee_interpretation(
    room, interpretation, event, *, captions_url: str = ""
) -> None:
    payload = attendee_interpretation_payload(interpretation, captions_url=captions_url)
    changed = False
    if payload is None:
        changed = clear_module_interpretation(room)
    else:
        changed = set_module_interpretation(room, payload)
    if changed:
        room.save(update_fields=["module_config"])
        _notify_room_config_changed(event)


def start_room_session(
    room, event, *, stream_url_override: str = "", captions_url: str = ""
) -> SessionResult:
    if not is_susi_configured(event):
        return SessionResult(
            ok=False,
            error=str(
                _(
                    "Connect and enable SUSI on the interpretation dashboard before starting a room."
                )
            ),
        )

    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)
    if not interpretation.room_enabled:
        return SessionResult(
            ok=False,
            error=str(_("Enable interpretation for this room first.")),
            interpretation=interpretation,
        )
    override = (stream_url_override or "").strip()
    stream_url = override or interpretation.stream_url or get_room_stream_url(room)
    if not stream_url:
        return SessionResult(
            ok=False,
            error=str(_("No stream URL is configured for this room.")),
            interpretation=interpretation,
        )

    client = get_susi_client(event)
    try:
        tenant_id = start_stream_session(
            client,
            stream_url,
            transcription_provider=interpretation.transcription_provider,
            translation_provider=interpretation.translation_provider,
        )
    except SusiError as exc:
        interpretation.status = RoomInterpretation.STATUS_IDLE
        interpretation.stream_url = stream_url
        interpretation.save()
        return SessionResult(ok=False, error=str(exc), interpretation=interpretation)

    interpretation.susi_session_id = tenant_id
    interpretation.stream_url = stream_url
    interpretation.status = RoomInterpretation.STATUS_RUNNING
    interpretation.save()
    if hasattr(interpretation, "log_action"):
        interpretation.log_action(
            "interpretation.room.started",
            data={"tenant_id": tenant_id, "stream_url": stream_url},
        )
    sync_attendee_interpretation(room, interpretation, event, captions_url=captions_url)
    return SessionResult(ok=True, interpretation=interpretation)


def stop_room_session(room, event) -> SessionResult:
    interpretation = get_interpretation(room)
    if interpretation is None or not interpretation.susi_session_id:
        return SessionResult(
            ok=False,
            error=str(_("No running interpretation session for this room.")),
        )

    client = get_susi_client(event)
    try:
        client.stop_session(interpretation.susi_session_id)
    except SusiError as exc:
        return SessionResult(ok=False, error=str(exc), interpretation=interpretation)

    if hasattr(interpretation, "log_action"):
        interpretation.log_action(
            "interpretation.room.stopped",
            data={"tenant_id": interpretation.susi_session_id},
        )
    interpretation.status = RoomInterpretation.STATUS_IDLE
    interpretation.susi_session_id = ""
    interpretation.save()
    sync_attendee_interpretation(room, interpretation, event, captions_url="")
    return SessionResult(ok=True, interpretation=interpretation)
