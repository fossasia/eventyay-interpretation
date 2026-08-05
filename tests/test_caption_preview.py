"""Tests for the temporary caption preview page."""

import pytest
from django.urls import reverse

from interpretation.models import RoomInterpretation
from interpretation.room_control import SessionResult
from interpretation.susi import SusiResult

pytestmark = pytest.mark.django_db


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def preview_url(event, room):
    return reverse(
        "plugins:interpretation:room.preview",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "pk": room.pk,
        },
    )


def preview_poll_url(event, room):
    return reverse(
        "plugins:interpretation:room.preview.poll",
        kwargs={
            "organizer": event.organizer.slug,
            "event": event.slug,
            "pk": room.pk,
        },
    )


def test_preview_page_is_simple(organizer_client, connected_event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    response = organizer_client.get(preview_url(connected_event, room))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Temporary" in content
    assert "Start" in content
    assert "Stop" in content
    assert "interpretation-preview-page" in content
    assert "Caption languages" not in content
    assert "Session ID" not in content


def test_preview_poll_requires_running_session(organizer_client, connected_event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_IDLE,
    )

    response = organizer_client.get(preview_poll_url(connected_event, room))

    assert response.status_code == 200
    assert response.json() == {"running": False, "text": "", "error": ""}


def test_preview_treats_legacy_stopped_status_as_not_running(
    organizer_client, connected_event, room
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_STOPPED,
        backend_session_id="stale-tenant",
    )

    response = organizer_client.get(preview_poll_url(connected_event, room))

    assert response.status_code == 200
    assert response.json() == {"running": False, "text": "", "error": ""}


def test_preview_poll_returns_transcript(
    organizer_client, connected_event, room, monkeypatch
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        backend_session_id="tenant-1",
    )

    class FakeClient:
        def latest_transcript(self, tenant_id):
            assert tenant_id == "tenant-1"
            return SusiResult(
                ok=True,
                status_code=200,
                data={"transcript": "hello world"},
            )

    monkeypatch.setattr(
        "interpretation.views.get_susi_client",
        lambda event: FakeClient(),
    )

    response = organizer_client.get(preview_poll_url(connected_event, room))

    assert response.status_code == 200
    assert response.json() == {
        "running": True,
        "text": "hello world",
        "error": "",
    }


def test_preview_start_action(organizer_client, connected_event, room, monkeypatch):
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    def fake_start(room_arg, event):
        return SessionResult(ok=True, interpretation=interpretation)

    monkeypatch.setattr("interpretation.views.start_room_session", fake_start)

    response = organizer_client.post(
        preview_url(connected_event, room),
        {"preview_action": "start"},
    )

    assert response.status_code == 302


def test_preview_caption_text_uses_transcript():
    from interpretation.views import _preview_caption_text

    assert _preview_caption_text({"transcript": "hello"}) == "hello"
    assert _preview_caption_text({"translation": "hallo"}) == "hallo"
