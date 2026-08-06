"""Integration tests for per-room commons POST actions."""

import pytest
from django.contrib.messages import get_messages

from interpretation.backend_credentials import is_susi_configured
from interpretation.forms import ROOM_ACTION_KEY, ROOM_ID_KEY
from interpretation.models import RoomInterpretation

pytestmark = pytest.mark.django_db


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def _room_post(room, action, **extra):
    prefix = f"room-{room.pk}"
    return {
        ROOM_ID_KEY: str(room.pk),
        ROOM_ACTION_KEY: action,
        f"{prefix}-interpreter": RoomInterpretation.INTERPRETER_SUSI,
        **extra,
    }


def test_room_save_persists_interpreter(
    organizer_client, connected_event, room, rooms_url
):
    response = organizer_client.post(
        rooms_url,
        _room_post(room, "save", **{f"room-{room.pk}-room_enabled": "on"}),
    )

    assert response.status_code == 302
    interpretation = RoomInterpretation.objects.get(room=room)
    assert interpretation.interpreter == RoomInterpretation.INTERPRETER_SUSI
    assert interpretation.room_enabled is True


def test_room_clear_removes_room_credentials(
    organizer_client, connected_room, rooms_url
):
    room = connected_room
    interpretation = RoomInterpretation.objects.get(room=room)

    response = organizer_client.post(
        rooms_url,
        _room_post(room, "disconnect"),
    )

    assert response.status_code == 302
    interpretation.refresh_from_db()
    assert not is_susi_configured(interpretation)
    assert interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE
    assert interpretation.room_enabled is False
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("cleared interpretation" in message.lower() for message in messages)


def test_room_start_action_is_not_supported(
    organizer_client, connected_room, rooms_url,
):
    room = connected_room

    response = organizer_client.post(
        rooms_url,
        _room_post(room, "start", **{f"room-{room.pk}-room_enabled": "on"}),
    )

    assert response.status_code == 302
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("unknown room action" in message.lower() for message in messages)


def test_room_stop_shows_warning_when_remote_stop_fails(
    monkeypatch, organizer_client, connected_event, room, rooms_url
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        backend_session_id="sess-1",
    )

    def fake_stop(room_arg, event):
        interpretation = RoomInterpretation.objects.get(room=room)
        from interpretation.room_control import SessionResult

        return SessionResult(
            ok=True,
            warning=(
                "Stopped interpretation for this room locally, but the "
                "interpreter backend reported: timeout"
            ),
            interpretation=interpretation,
        )

    monkeypatch.setattr("interpretation.views.stop_room_session", fake_stop)

    response = organizer_client.post(rooms_url, _room_post(room, "stop"))

    assert response.status_code == 302
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("stopped interpretation for" in message.lower() for message in messages)
    assert any(
        "interpreter backend reported" in message.lower() for message in messages
    )
    assert not any("could not stop" in message.lower() for message in messages)
