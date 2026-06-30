"""Shared per-room interpretation helpers for commons views and video admin API."""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .models import RoomInterpretation
from .services import start_stream_session
from .settings import get_susi_client, is_susi_configured, is_susi_connected, is_interpretation_enabled
from .susi import SusiError
from .utils import get_room_stream_url, normalize_target_languages, interpretation_dashboard_url

PLUGIN_MODULE = "interpretation"


def plugin_enabled(event) -> bool:
    return PLUGIN_MODULE in event.get_plugins()


def get_interpretation(room) -> RoomInterpretation | None:
    return RoomInterpretation.objects.filter(room=room).first()


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
        "status": interpretation.status
        if interpretation
        else RoomInterpretation.STATUS_IDLE,
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
    interpretation.save()
    return interpretation


@dataclass
class SessionResult:
    ok: bool
    error: str = ""
    interpretation: RoomInterpretation | None = None


def start_room_session(
    room, event, *, stream_url_override: str = ""
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
        interpretation.status = RoomInterpretation.STATUS_ERROR
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
    interpretation.status = RoomInterpretation.STATUS_STOPPED
    interpretation.susi_session_id = ""
    interpretation.save()
    return SessionResult(ok=True, interpretation=interpretation)
