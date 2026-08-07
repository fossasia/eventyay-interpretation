"""Aggregate interpretation status for the plugin overview landing page."""

from __future__ import annotations

from .backends import get_backend, list_available_interpreters
from .models import RoomInterpretation
from .room_control import is_room_interpretation_ready, normalize_session_status


def build_overview_context(event) -> dict:
    """Return template context for the interpretation overview landing page."""
    rooms_qs = event.rooms.filter(deleted=False).order_by("name")
    interpretations = {
        ri.room_id: ri
        for ri in RoomInterpretation.objects.filter(room__event=event).select_related(
            "room"
        )
    }

    room_total = rooms_qs.count()
    room_enabled = 0
    room_running = 0
    room_needs_setup = 0
    interpreters_in_use: dict[str, int] = {}
    running_sessions = []
    room_snapshots = []

    for room in rooms_qs:
        interpretation = interpretations.get(room.pk)
        data_interpreter = RoomInterpretation.INTERPRETER_NONE
        room_on = False
        status = RoomInterpretation.STATUS_IDLE
        interpreter_label = str(get_backend(RoomInterpretation.INTERPRETER_NONE).label)
        target_languages: list[str] = []
        interpreter_ready = False

        if interpretation:
            data_interpreter = interpretation.interpreter
            room_on = interpretation.room_enabled
            status = normalize_session_status(interpretation.status)
            interpreter_label = str(get_backend(interpretation.interpreter).label)
            target_languages = list(interpretation.target_languages or [])
            interpreter_ready = is_room_interpretation_ready(
                room, event, interpretation
            )

        if room_on:
            room_enabled += 1
        if status == RoomInterpretation.STATUS_RUNNING:
            room_running += 1
            running_sessions.append(
                {
                    "room": room,
                    "interpreter_label": interpreter_label,
                    "target_languages": target_languages,
                }
            )
        if (
            room_on
            and data_interpreter != RoomInterpretation.INTERPRETER_NONE
            and not interpreter_ready
        ):
            room_needs_setup += 1
        if room_on and data_interpreter != RoomInterpretation.INTERPRETER_NONE:
            interpreters_in_use[data_interpreter] = (
                interpreters_in_use.get(data_interpreter, 0) + 1
            )

        if status == RoomInterpretation.STATUS_RUNNING:
            snapshot_status = "running"
        elif room_on:
            snapshot_status = "ready" if interpreter_ready else "setup"
        else:
            snapshot_status = "off"

        room_snapshots.append(
            {
                "room": room,
                "status": snapshot_status,
                "interpreter_label": interpreter_label
                if data_interpreter != RoomInterpretation.INTERPRETER_NONE
                else None,
            }
        )

    interpreter_usage = []
    for interpreter_id, count in sorted(interpreters_in_use.items()):
        backend = get_backend(interpreter_id)
        interpreter_usage.append(
            {
                "id": interpreter_id,
                "label": str(backend.label),
                "room_count": count,
            }
        )

    backends = []
    for backend in list_available_interpreters(event):
        if backend["id"] == RoomInterpretation.INTERPRETER_NONE:
            continue
        backends.append(
            {
                **backend,
                "rooms_using": interpreters_in_use.get(backend["id"], 0),
            }
        )

    return {
        "stats": {
            "room_total": room_total,
            "room_enabled": room_enabled,
            "room_running": room_running,
            "room_needs_setup": room_needs_setup,
        },
        "interpreter_usage": interpreter_usage,
        "backends": backends,
        "running_sessions": running_sessions,
        "room_snapshots": room_snapshots,
        "setup_complete": room_total > 0 and room_enabled > 0 and room_needs_setup == 0,
    }
