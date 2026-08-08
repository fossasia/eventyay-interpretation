"""Tests for the temporary caption preview page."""

import json

import pytest
from django.urls import reverse

from interpretation.models import RoomInterpretation
from interpretation.room_control import SessionResult

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


def preview_stream_url(event, room):
    return reverse(
        "plugins:interpretation:room.preview.stream",
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
    assert "Transcription provider" in content
    assert "Translation provider" in content
    assert "Caption language" not in content
    assert "Session ID" not in content


def test_preview_page_includes_sse_when_running(organizer_client, connected_event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        backend_session_id="tenant-1",
    )

    response = organizer_client.get(preview_url(connected_event, room))

    assert response.status_code == 200
    content = response.content.decode()
    assert "EventSource" in content
    assert "preview/stream" in content


def test_preview_stream_requires_running_session(organizer_client, connected_event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_IDLE,
    )

    response = organizer_client.get(preview_stream_url(connected_event, room))

    assert response.status_code == 409
    assert response.json()["status"] == "error"


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

    response = organizer_client.get(preview_stream_url(connected_event, room))

    assert response.status_code == 409


def test_preview_stream_proxies_sse(organizer_client, connected_event, room, monkeypatch):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        backend_session_id="tenant-1",
        target_languages=["de"],
    )

    class FakeClient:
        def iter_caption_stream(self, tenant_id, *, target_lang=""):
            assert tenant_id == "tenant-1"
            assert target_lang == "de"
            payload = json.dumps({"transcript": "hello world", "translation": "hallo"})
            yield f"data: {payload}\n\n".encode()

    monkeypatch.setattr(
        "interpretation.views.get_susi_client",
        lambda event: FakeClient(),
    )

    response = organizer_client.get(preview_stream_url(connected_event, room))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    body = b"".join(response.streaming_content)
    assert b"hello world" in body


def _preview_settings_post():
    return {
        "preview_action": "start",
        "transcription_provider": "faster_whisper",
        "translation_provider": "nllb_ctranslate2",
    }


def test_preview_save_settings(organizer_client, connected_event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    response = organizer_client.post(
        preview_url(connected_event, room),
        {
            "preview_action": "save_settings",
            "transcription_provider": "faster_whisper",
            "translation_provider": "nllb_ctranslate2",
        },
    )

    assert response.status_code == 302
    interpretation = RoomInterpretation.objects.get(room=room)
    assert interpretation.transcription_provider == "faster_whisper"
    assert interpretation.translation_provider == "nllb_ctranslate2"


def test_preview_start_action(organizer_client, connected_event, room, monkeypatch):
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    def fake_start(room_arg, event, *, stream_url_override=""):
        return SessionResult(ok=True, interpretation=interpretation)

    monkeypatch.setattr("interpretation.views.start_room_session", fake_start)

    response = organizer_client.post(
        preview_url(connected_event, room),
        _preview_settings_post(),
    )

    assert response.status_code == 302
    interpretation.refresh_from_db()
    assert interpretation.transcription_provider == "faster_whisper"


def test_preview_caption_text_uses_transcript():
    from interpretation.views import _preview_caption_text

    assert _preview_caption_text({"transcript": "hello"}) == "hello"
    assert _preview_caption_text({"translation": "hallo"}) == "hallo"
