"""Tests for per-room interpreter dispatch and readiness."""

import pytest

from interpretation.backends import INTERPRETER_NONE, INTERPRETER_SUSI, get_backend
from interpretation.models import RoomInterpretation
from interpretation.room_control import (
    is_room_interpretation_ready,
    normalize_session_status,
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
    update_room_interpretation,
)
from tests.conftest import apply_susi_event_credentials


class _FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, as_type=str):
        if key not in self._data:
            return default
        value = self._data[key]
        if as_type is bool:
            return bool(value)
        return as_type(value)

    def set(self, key, value):
        self._data[key] = value


class _FakeEvent:
    def __init__(self, settings=None, plugins=None):
        self.settings = _FakeSettings(settings)
        self.organizer = type("O", (), {"slug": "org"})()
        self.slug = "evt"

    def get_plugins(self):
        return ["interpretation"]


def _event_with_susi(token="tok"):
    return _FakeEvent(
        {
            "interpretation_is_enabled": True,
            "interpretation_susi_base_url": "https://susi.example.com",
            "interpretation_susi_auth_token": token,
        }
    )


class _FakeRoom:
    pk = 1
    module_config = [
        {"type": "livestream.native", "config": {"hls_url": "https://x/r.m3u8"}}
    ]
    stream_schedules = None


class _FakeInterpretation:
    def __init__(self, **kwargs):
        self.room = _FakeRoom()
        self.interpreter = kwargs.get("interpreter", INTERPRETER_NONE)
        self.room_enabled = kwargs.get("room_enabled", False)
        self.stream_url = kwargs.get("stream_url", "")
        self.source_language = kwargs.get("source_language", "")
        self.target_languages = kwargs.get("target_languages", [])
        self.transcription_provider = ""
        self.translation_provider = ""
        self.backend_config = {}
        self.backend_session_id = kwargs.get("backend_session_id", "")
        self.status = kwargs.get("status", RoomInterpretation.STATUS_IDLE)
        self.saved = 0

    def save(self, update_fields=None):
        self.saved += 1

    def refresh_from_db(self, using=None, fields=None):
        return None


def test_get_backend_defaults_to_none():
    backend = get_backend("unknown")
    assert backend.id == INTERPRETER_NONE


def test_normalize_session_status_maps_legacy_values():
    assert normalize_session_status(RoomInterpretation.STATUS_RUNNING) == "running"
    assert normalize_session_status(RoomInterpretation.STATUS_IDLE) == "idle"
    assert normalize_session_status(RoomInterpretation.STATUS_STOPPED) == "idle"
    assert normalize_session_status(RoomInterpretation.STATUS_ERROR) == "idle"


def test_serialize_hides_session_id_when_not_running():
    event = _FakeEvent()
    interpretation = _FakeInterpretation(
        interpreter=INTERPRETER_SUSI,
        backend_session_id="tenant-stale",
        status=RoomInterpretation.STATUS_IDLE,
    )
    data = serialize_room_interpretation(_FakeRoom(), event, interpretation)
    assert data["status"] == "idle"
    assert data["session_id"] == ""


def test_serialize_includes_session_id_when_running():
    event = _FakeEvent()
    interpretation = _FakeInterpretation(
        interpreter=INTERPRETER_SUSI,
        backend_session_id="tenant-live",
        status=RoomInterpretation.STATUS_RUNNING,
    )
    data = serialize_room_interpretation(_FakeRoom(), event, interpretation)
    assert data["status"] == "running"
    assert data["session_id"] == "tenant-live"


def test_is_room_interpretation_ready_requires_enabled_interpreter_and_credentials():
    event = _FakeEvent()
    off = _FakeInterpretation(room_enabled=False, interpreter=INTERPRETER_SUSI)
    assert is_room_interpretation_ready(_FakeRoom(), event, off) is False

    none = _FakeInterpretation(room_enabled=True, interpreter=INTERPRETER_NONE)
    assert is_room_interpretation_ready(_FakeRoom(), event, none) is False

    no_creds = _FakeInterpretation(room_enabled=True, interpreter=INTERPRETER_SUSI)
    assert is_room_interpretation_ready(_FakeRoom(), event, no_creds) is False

    configured = _FakeInterpretation(room_enabled=True, interpreter=INTERPRETER_SUSI)
    assert is_room_interpretation_ready(
        _FakeRoom(), _event_with_susi(), configured
    ) is True


def test_start_room_session_rejects_disabled_event(monkeypatch):
    event = _FakeEvent({"interpretation_is_enabled": False})
    interpretation = _FakeInterpretation(
        room_enabled=True, interpreter=INTERPRETER_SUSI
    )

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    result = start_room_session(_FakeRoom(), event)
    assert not result.ok
    assert "turned off" in result.error.lower()


def test_start_room_session_rejects_disabled_room(monkeypatch):
    interpretation = _FakeInterpretation(
        room_enabled=False, interpreter=INTERPRETER_SUSI
    )

    def fake_get_or_create(room):
        return interpretation, False

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: fake_get_or_create(room),
    )
    result = start_room_session(_FakeRoom(), _FakeEvent())
    assert not result.ok
    assert "disabled" in result.error.lower()


def test_start_room_session_rejects_missing_interpreter(monkeypatch):
    interpretation = _FakeInterpretation(
        room_enabled=True, interpreter=INTERPRETER_NONE
    )

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    result = start_room_session(_FakeRoom(), _FakeEvent())
    assert not result.ok
    assert "interpreter" in result.error.lower()


def test_start_room_session_uses_selected_backend(monkeypatch):
    event = _event_with_susi()
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
    )
    calls = []

    class FakeBackend:
        id = INTERPRETER_SUSI
        label = "SUSI"

        def is_configured(self, event):
            return True

        def start(self, event, interpretation, *, stream_url):
            calls.append(stream_url)
            return "tenant-42"

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    monkeypatch.setattr(
        "interpretation.room_control.get_backend",
        lambda interpreter_id: FakeBackend(),
    )

    result = start_room_session(_FakeRoom(), event)
    assert result.ok
    assert calls == ["https://x/r.m3u8"]
    assert interpretation.backend_session_id == "tenant-42"
    assert interpretation.status == RoomInterpretation.STATUS_RUNNING


def test_start_room_session_uses_event_credentials(monkeypatch):
    event = _event_with_susi(token="event-token")
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
    )
    calls = []

    def fake_start_stream_session(client, stream_url, **kwargs):
        calls.append(client.auth_token)
        return "tenant-room"

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    monkeypatch.setattr(
        "interpretation.backends.susi.start_stream_session",
        fake_start_stream_session,
    )

    result = start_room_session(_FakeRoom(), event)
    assert result.ok
    assert calls == ["event-token"]


def test_start_room_session_uses_shared_event_credentials_for_all_rooms(
    monkeypatch, event
):
    apply_susi_event_credentials(event)

    from eventyay.base.models import Room

    room_a = Room(pk=1, module_config=[])
    room_b = Room(pk=2, module_config=[])
    interpretation_a = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
    )
    interpretation_a.room = room_a
    interpretation_b = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
    )
    interpretation_b.room = room_b
    by_room = {1: interpretation_a, 2: interpretation_b}
    tokens = []

    def fake_get_or_create(room):
        return by_room[room.pk], False

    def fake_start_stream_session(client, stream_url, **kwargs):
        tokens.append(client.auth_token)
        return f"sess-{client.auth_token}"

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        fake_get_or_create,
    )
    monkeypatch.setattr(
        "interpretation.backends.susi.start_stream_session",
        fake_start_stream_session,
    )

    class _RoomWithStream:
        pk = 1
        module_config = [
            {
                "type": "livestream.native",
                "config": {"hls_url": "https://stream.example.com/a.m3u8"},
            }
        ]
        stream_schedules = None

    result = start_room_session(_RoomWithStream(), event)
    assert result.ok
    assert tokens == ["jwt-test-token"]
    assert interpretation_a.backend_session_id == "sess-jwt-test-token"
    assert interpretation_b.backend_session_id == ""


def test_update_room_interpretation_changes_interpreter_selection(monkeypatch):
    event = _event_with_susi()
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
    )

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )

    update_room_interpretation(
        _FakeRoom(),
        event,
        {"interpreter": INTERPRETER_NONE},
    )

    assert interpretation.interpreter == INTERPRETER_NONE


def test_start_room_session_is_idempotent_when_already_running(monkeypatch):
    event = _event_with_susi()
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
        backend_session_id="tenant-1",
        status=RoomInterpretation.STATUS_RUNNING,
    )
    calls = []

    class FakeBackend:
        id = INTERPRETER_SUSI
        label = "SUSI"

        def is_configured(self, event):
            return True

        def start(self, event, interpretation, *, stream_url):
            calls.append(stream_url)
            return "tenant-2"

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    monkeypatch.setattr(
        "interpretation.room_control.get_backend",
        lambda interpreter_id: FakeBackend(),
    )

    result = start_room_session(_FakeRoom(), event)
    assert result.ok
    assert calls == []
    assert interpretation.backend_session_id == "tenant-1"


def test_start_room_session_clears_stale_session_id_on_failure(monkeypatch):
    event = _event_with_susi()
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
        backend_session_id="tenant-old",
        status=RoomInterpretation.STATUS_IDLE,
    )

    class FakeBackend:
        def is_configured(self, event):
            return True

        def start(self, event, interpretation, *, stream_url):
            from interpretation.susi import SusiError

            raise SusiError("configure failed")

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    monkeypatch.setattr(
        "interpretation.room_control.get_backend",
        lambda interpreter_id: FakeBackend(),
    )

    result = start_room_session(_FakeRoom(), event)
    assert not result.ok
    assert interpretation.backend_session_id == ""
    assert interpretation.status == RoomInterpretation.STATUS_IDLE


def test_stop_room_session_clears_local_state_when_remote_stop_fails(monkeypatch):
    event = _FakeEvent()
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
        backend_session_id="tenant-1",
        status=RoomInterpretation.STATUS_RUNNING,
    )

    class FakeBackend:
        def stop(self, event, interpretation):
            from interpretation.susi import SusiError

            raise SusiError("SUSI unreachable")

    monkeypatch.setattr(
        "interpretation.room_control.get_interpretation",
        lambda room: interpretation,
    )
    monkeypatch.setattr(
        "interpretation.room_control.get_backend",
        lambda interpreter_id: FakeBackend(),
    )

    result = stop_room_session(_FakeRoom(), event)
    assert result.ok
    assert "SUSI unreachable" in result.warning
    assert interpretation.status == RoomInterpretation.STATUS_IDLE
    assert interpretation.backend_session_id == ""


def test_update_room_interpretation_raises_when_auto_stop_fails(monkeypatch):
    event = _FakeEvent()
    interpretation = _FakeInterpretation(
        room_enabled=True,
        interpreter=INTERPRETER_SUSI,
        backend_session_id="tenant-1",
        status=RoomInterpretation.STATUS_RUNNING,
    )

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, False),
    )
    monkeypatch.setattr(
        "interpretation.room_control.stop_room_session",
        lambda room, event: type(
            "R",
            (),
            {
                "ok": False,
                "error": "SUSI unreachable",
                "interpretation": interpretation,
            },
        )(),
    )

    with pytest.raises(ValueError, match="SUSI unreachable"):
        update_room_interpretation(
            _FakeRoom(),
            event,
            {"interpreter": INTERPRETER_NONE},
        )


def test_update_room_interpretation_allows_unconfigured_interpreter(monkeypatch):
    event = _FakeEvent()
    interpretation = _FakeInterpretation()

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        lambda room: (interpretation, True),
    )

    result = update_room_interpretation(
        _FakeRoom(), event, {"interpreter": INTERPRETER_SUSI}
    )
    assert result.interpreter == INTERPRETER_SUSI


def test_clear_room_interpretation_setup_resets_room_only(monkeypatch):
    from interpretation.room_control import clear_room_interpretation_setup

    interpretation = _FakeInterpretation(
        interpreter=INTERPRETER_SUSI,
        room_enabled=True,
        backend_session_id="",
    )
    calls = []

    def fake_get_or_create(room):
        return interpretation, False

    def fake_update(room, event, data):
        calls.append(data)
        interpretation.interpreter = data["interpreter"]
        interpretation.room_enabled = data["room_enabled"]
        return interpretation

    monkeypatch.setattr(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        fake_get_or_create,
    )
    monkeypatch.setattr(
        "interpretation.room_control.update_room_interpretation",
        fake_update,
    )

    result = clear_room_interpretation_setup(_FakeRoom(), _FakeEvent())
    assert calls == [
        {
            "interpreter": INTERPRETER_NONE,
            "room_enabled": False,
        }
    ]
    assert result.interpreter == INTERPRETER_NONE
    assert result.room_enabled is False
