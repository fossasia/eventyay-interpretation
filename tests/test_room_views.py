"""Integration tests for per-room commons POST actions."""

import pytest
from django.contrib.messages import get_messages

from interpretation.forms import ROOM_ACTION_KEY, ROOM_ID_KEY
from interpretation.models import RoomInterpretation
from interpretation.settings import get_auth_token

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


def test_room_clear_keeps_event_susi_token(
    organizer_client, connected_event, room, rooms_url
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    response = organizer_client.post(
        rooms_url,
        _room_post(room, "disconnect"),
    )

    assert response.status_code == 302
    connected_event.settings.flush()
    assert get_auth_token(connected_event) == "jwt-test-token"
    interpretation = RoomInterpretation.objects.get(room=room)
    assert interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE
    assert interpretation.room_enabled is False
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("susi stays connected" in message.lower() for message in messages)


def test_room_start_requires_configured_susi(organizer_client, event, room, rooms_url):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    response = organizer_client.post(
        rooms_url,
        _room_post(room, "start", **{f"room-{room.pk}-room_enabled": "on"}),
    )

    assert response.status_code == 302
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("sign in" in message.lower() for message in messages)


def test_room_start_with_susi(
    monkeypatch, organizer_client, connected_event, room, rooms_url
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        transcription_provider="whisper_local",
        translation_provider="nllb_local",
    )

    def fake_start(room_arg, event, *, stream_url_override=""):
        interpretation = RoomInterpretation.objects.get(room=room)
        from interpretation.room_control import SessionResult

        return SessionResult(ok=True, interpretation=interpretation)

    monkeypatch.setattr("interpretation.views.start_room_session", fake_start)

    response = organizer_client.post(
        rooms_url,
        _room_post(room, "start", **{f"room-{room.pk}-room_enabled": "on"}),
    )

    assert response.status_code == 302
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("started" in message.lower() for message in messages)
