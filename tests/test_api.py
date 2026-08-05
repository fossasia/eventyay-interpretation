"""API tests for per-room interpretation endpoints."""

import pytest

from interpretation.models import RoomInterpretation
from tests.conftest import SUSI_BACKEND_CONFIG

pytestmark = pytest.mark.django_db


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def _api_paths(event, room, action):
    org = event.organizer.slug
    slug = event.slug
    suffix = {"config": "config/", "start": "start/", "stop": "stop/"}[action]
    return [
        f"/api/v1/organizers/{org}/events/{slug}/rooms/{room.pk}/interpretation/{suffix}",
        f"/api/v1/events/{slug}/rooms/{room.pk}/interpretation/{suffix}",
    ]


def _api_request(client, event, room, action, *, method="get", data=None):
    last = None
    for path in _api_paths(event, room, action):
        if method == "get":
            last = client.get(path)
        elif method == "patch":
            last = client.patch(path, data=data, content_type="application/json")
        else:
            last = client.post(path, data=data or {}, content_type="application/json")
        if last.status_code != 404:
            return last
    return last


def test_api_config_get(organizer_client, connected_event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        target_languages=["de"],
        backend_config=dict(SUSI_BACKEND_CONFIG),
    )

    response = _api_request(
        organizer_client, connected_event, room, "config", method="get"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["interpreter"] == RoomInterpretation.INTERPRETER_SUSI
    assert payload["target_languages"] == ["de"]
    assert payload["susi_connected"] is True
    assert payload["session_id"] == ""


def test_api_config_patch(organizer_client, connected_event, room):
    response = _api_request(
        organizer_client,
        connected_event,
        room,
        "config",
        method="patch",
        data={
            "interpreter": RoomInterpretation.INTERPRETER_SUSI,
            "room_enabled": True,
            "target_languages": ["fr", "de"],
            "transcription_provider": "whisper_local",
            "translation_provider": "nllb_local",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_languages"] == ["fr", "de"]
    assert payload["transcription_provider"] == "whisper_local"


def test_api_start_and_stop(organizer_client, connected_event, room, monkeypatch):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        transcription_provider="whisper_local",
        translation_provider="nllb_local",
        target_languages=["en"],
        backend_config=dict(SUSI_BACKEND_CONFIG),
    )

    def fake_start(room_arg, event, *, stream_url_override=""):
        interpretation = RoomInterpretation.objects.get(room=room)
        interpretation.backend_session_id = "tenant-api"
        interpretation.status = RoomInterpretation.STATUS_RUNNING
        interpretation.save()
        from interpretation.room_control import SessionResult

        return SessionResult(ok=True, interpretation=interpretation)

    def fake_stop(room_arg, event):
        interpretation = RoomInterpretation.objects.get(room=room)
        interpretation.backend_session_id = ""
        interpretation.status = RoomInterpretation.STATUS_IDLE
        interpretation.save()
        from interpretation.room_control import SessionResult

        return SessionResult(ok=True, interpretation=interpretation)

    monkeypatch.setattr("interpretation.api.start_room_session", fake_start)
    monkeypatch.setattr("interpretation.api.stop_room_session", fake_stop)

    start = _api_request(
        organizer_client,
        connected_event,
        room,
        "start",
        method="post",
        data={"stream_url": "https://stream.example.com/live.m3u8"},
    )
    assert start.status_code == 200
    assert start.json()["session_id"] == "tenant-api"

    stop = _api_request(organizer_client, connected_event, room, "stop", method="post")
    assert stop.status_code == 200
    assert stop.json()["session_id"] == ""


def test_api_stop_returns_warning_when_remote_stop_fails(
    monkeypatch, organizer_client, connected_event, room
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        backend_session_id="sess-1",
        backend_config=dict(SUSI_BACKEND_CONFIG),
    )

    def fake_stop(room_arg, event):
        interpretation = RoomInterpretation.objects.get(room=room)
        from interpretation.room_control import SessionResult

        return SessionResult(
            ok=True,
            warning="Stopped locally, but SUSI reported: timeout",
            interpretation=interpretation,
        )

    monkeypatch.setattr("interpretation.api.stop_room_session", fake_stop)

    response = _api_request(
        organizer_client, connected_event, room, "stop", method="post"
    )
    assert response.status_code == 200
    assert response.json()["warning"] == "Stopped locally, but SUSI reported: timeout"


def test_api_rejects_unknown_interpreter(organizer_client, connected_event, room):
    response = _api_request(
        organizer_client,
        connected_event,
        room,
        "config",
        method="patch",
        data={"interpreter": "evil"},
    )

    assert response.status_code == 400
