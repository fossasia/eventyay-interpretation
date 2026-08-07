"""Shared per-room interpretation helpers for commons views and video admin API."""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from .backends import get_backend, list_available_interpreters
from .interpreter_credentials import (
    SUSI_CREDENTIAL_KEYS,
    is_susi_configured,
    strip_room_credential_keys,
)
from .models import RoomInterpretation
from .settings import is_interpretation_enabled
from .susi import SusiError
from .utils import (
    get_room_stream_url,
    interpretation_dashboard_url,
    normalize_target_languages,
    validate_backend_config,
    validate_target_language_codes,
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


def _public_session_id(interpretation: RoomInterpretation | None) -> str:
    if interpretation is None:
        return ""
    status = normalize_session_status(interpretation.status)
    if status != RoomInterpretation.STATUS_RUNNING:
        return ""
    return interpretation.backend_session_id or ""


def is_room_interpretation_ready(
    room, event, interpretation: RoomInterpretation | None = None
) -> bool:
    if interpretation is None:
        interpretation = get_interpretation(room)
    if interpretation is None or not interpretation.room_enabled:
        return False
    if interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE:
        return False
    return get_backend(interpretation.interpreter).is_configured(event)


def _public_backend_config(interpretation: RoomInterpretation | None) -> dict:
    if interpretation is None:
        return {}
    return strip_room_credential_keys(interpretation.backend_config)


def serialize_room_interpretation(room, event, interpretation=None) -> dict:
    if interpretation is None:
        interpretation = get_interpretation(room)
    detected_stream_url = get_room_stream_url(room)
    stream_url = ""
    interpreter = RoomInterpretation.INTERPRETER_NONE
    room_enabled = False
    if interpretation:
        stream_url = interpretation.stream_url or ""
        interpreter = interpretation.interpreter
        room_enabled = interpretation.room_enabled
    backend = get_backend(interpreter)
    return {
        "interpreter": interpreter,
        "interpreter_label": str(backend.label),
        "room_enabled": room_enabled,
        "interpreter_ready": is_room_interpretation_ready(room, event, interpretation),
        "available_interpreters": list_available_interpreters(event),
        "target_languages": list(interpretation.target_languages or [])
        if interpretation
        else [],
        "transcription_provider": interpretation.transcription_provider
        if interpretation
        else "",
        "translation_provider": interpretation.translation_provider
        if interpretation
        else "",
        "backend_config": _public_backend_config(interpretation),
        "status": normalize_session_status(
            interpretation.status if interpretation else RoomInterpretation.STATUS_IDLE
        ),
        "session_id": _public_session_id(interpretation),
        "stream_url": stream_url or detected_stream_url,
        "detected_stream_url": detected_stream_url,
        "plugin_enabled": plugin_enabled(event),
        "susi_connected": is_susi_configured(event),
        "dashboard_url": interpretation_dashboard_url(event.organizer.slug, event.slug),
    }


def _merge_public_backend_config(
    interpretation: RoomInterpretation, incoming: dict
) -> dict:
    """Merge non-credential backend_config keys; credentials are sign-in only."""
    config = strip_room_credential_keys(interpretation.backend_config)
    for key, value in validate_backend_config(incoming).items():
        if key in SUSI_CREDENTIAL_KEYS:
            continue
        config[key] = value
    return config


def _apply_backend_config(interpretation: RoomInterpretation, data: dict) -> None:
    if "backend_config" in data:
        interpretation.backend_config = _merge_public_backend_config(
            interpretation,
            data["backend_config"],
        )
    if "transcription_provider" in data:
        interpretation.transcription_provider = (
            data.get("transcription_provider") or ""
        ).strip()
    if "translation_provider" in data:
        interpretation.translation_provider = (
            data.get("translation_provider") or ""
        ).strip()


def update_room_interpretation(room, event, data: dict) -> RoomInterpretation:
    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)
    was_running = bool(interpretation.backend_session_id)
    old_interpreter = interpretation.interpreter

    if "interpreter" in data:
        interpreter = (
            data.get("interpreter") or RoomInterpretation.INTERPRETER_NONE
        ).strip()
        if interpreter not in {
            RoomInterpretation.INTERPRETER_NONE,
            RoomInterpretation.INTERPRETER_SUSI,
        }:
            raise ValueError(_("Unknown interpreter."))
        interpretation.interpreter = interpreter

    if "room_enabled" in data:
        interpretation.room_enabled = bool(data.get("room_enabled"))

    if "target_languages" in data:
        interpretation.target_languages = validate_target_language_codes(
            normalize_target_languages(data.get("target_languages"))
        )

    _apply_backend_config(interpretation, data)
    interpretation.save()

    if was_running and (
        not interpretation.room_enabled
        or interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE
        or interpretation.interpreter != old_interpreter
    ):
        result = stop_room_session(room, event)
        interpretation.refresh_from_db()
        if not result.ok:
            raise ValueError(result.error)

    return interpretation


@dataclass
class SessionResult:
    ok: bool
    error: str = ""
    warning: str = ""
    interpretation: RoomInterpretation | None = None


def start_room_session(room, event, *, stream_url_override: str = "") -> SessionResult:
    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)

    if not is_interpretation_enabled(event):
        return SessionResult(
            ok=False,
            error=str(_("Live interpretation is turned off for this event.")),
            interpretation=interpretation,
        )

    if not interpretation.room_enabled:
        return SessionResult(
            ok=False,
            error=str(_("Interpretation is disabled for this room.")),
            interpretation=interpretation,
        )

    if interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE:
        return SessionResult(
            ok=False,
            error=str(_("Select an interpreter for this room before starting.")),
            interpretation=interpretation,
        )

    backend = get_backend(interpretation.interpreter)
    if not backend.is_configured(event):
        return SessionResult(
            ok=False,
            error=str(
                _("Configure %(name)s under Configure interpreters before starting.")
                % {"name": backend.label}
            ),
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

    if (
        interpretation.backend_session_id
        and normalize_session_status(interpretation.status)
        == RoomInterpretation.STATUS_RUNNING
    ):
        return SessionResult(ok=True, interpretation=interpretation)

    try:
        session_id = backend.start(event, interpretation, stream_url=stream_url)
    except (SusiError, ValueError) as exc:
        interpretation.status = normalize_session_status(RoomInterpretation.STATUS_IDLE)
        interpretation.backend_session_id = ""
        interpretation.stream_url = stream_url
        interpretation.save()
        return SessionResult(ok=False, error=str(exc), interpretation=interpretation)

    interpretation.backend_session_id = session_id
    interpretation.stream_url = stream_url
    interpretation.status = normalize_session_status(RoomInterpretation.STATUS_RUNNING)
    interpretation.save()
    if hasattr(interpretation, "log_action"):
        interpretation.log_action(
            "interpretation.room.started",
            data={
                "interpreter": interpretation.interpreter,
                "session_id": session_id,
                "stream_url": stream_url,
            },
        )
    return SessionResult(ok=True, interpretation=interpretation)


def clear_room_interpretation_setup(room, event) -> RoomInterpretation:
    """Stop this room's session and reset interpreter selection."""
    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)
    if interpretation.backend_session_id:
        stop_room_session(room, event)
        interpretation.refresh_from_db()
    return update_room_interpretation(
        room,
        event,
        {
            "interpreter": RoomInterpretation.INTERPRETER_NONE,
            "room_enabled": False,
        },
    )


def _clear_local_session(
    interpretation: RoomInterpretation, *, session_id: str = ""
) -> None:
    if hasattr(interpretation, "log_action") and session_id:
        interpretation.log_action(
            "interpretation.room.stopped",
            data={
                "interpreter": interpretation.interpreter,
                "session_id": session_id,
            },
        )
    interpretation.status = normalize_session_status(RoomInterpretation.STATUS_IDLE)
    interpretation.backend_session_id = ""
    interpretation.save()


def stop_room_session(room, event) -> SessionResult:
    interpretation = get_interpretation(room)
    if interpretation is None or not interpretation.backend_session_id:
        return SessionResult(
            ok=False,
            error=str(_("No running interpretation session for this room.")),
        )

    backend = get_backend(interpretation.interpreter)
    session_id = interpretation.backend_session_id
    remote_error = ""
    try:
        backend.stop(event, interpretation)
    except SusiError as exc:
        remote_error = str(exc)

    _clear_local_session(interpretation, session_id=session_id)
    if remote_error:
        return SessionResult(
            ok=True,
            warning=str(
                _(
                    "Stopped interpretation for this room locally, but the "
                    "interpreter backend reported: %(error)s"
                )
                % {"error": remote_error}
            ),
            interpretation=interpretation,
        )
    return SessionResult(ok=True, interpretation=interpretation)


def stop_all_event_sessions(event) -> None:
    """Stop every room session for an event (e.g. when interpretation is disabled)."""
    interpretations = (
        RoomInterpretation.objects.filter(room__event=event)
        .exclude(backend_session_id="")
        .select_related("room")
    )
    for interpretation in interpretations:
        stop_room_session(interpretation.room, event)
