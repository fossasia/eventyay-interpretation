"""Tests for the HLS extraction helper and the start-session service."""

import pytest

from interpretation.services import source_for, start_stream_session
from interpretation.utils import (
    get_module_hls_url,
    get_room_hls_url,
    get_schedule_hls_url,
)


class FakeRoom:
    def __init__(self, module_config=None, schedules=None):
        self.module_config = module_config
        self.stream_schedules = FakeScheduleManager(schedules or [])


class FakeSchedule:
    def __init__(self, url, stream_type="hls", start_time=0, active=False):
        self.url = url
        self.stream_type = stream_type
        self.start_time = start_time
        self._active = active

    def is_active(self, at_time=None):
        return self._active


class FakeScheduleManager:
    def __init__(self, items):
        self._items = items

    def filter(self, **kwargs):
        stream_type = kwargs.get("stream_type")
        items = [s for s in self._items if s.stream_type == stream_type]
        return FakeScheduleManager(items)

    def __iter__(self):
        return iter(self._items)

    def order_by(self, key):
        reverse = key.startswith("-")
        field = key.lstrip("-")
        return FakeScheduleManager(
            sorted(self._items, key=lambda s: getattr(s, field), reverse=reverse)
        )

    def first(self):
        return self._items[0] if self._items else None


# -- module_config extraction ------------------------------------------


def test_module_hls_url_from_native_livestream():
    room = FakeRoom(
        module_config=[
            {"type": "chat.native", "config": {}},
            {"type": "livestream.native", "config": {"hls_url": "https://x/r.m3u8"}},
        ]
    )
    assert get_module_hls_url(room) == "https://x/r.m3u8"


def test_module_hls_url_absent_returns_empty():
    room = FakeRoom(module_config=[{"type": "call.bigbluebutton", "config": {}}])
    assert get_module_hls_url(room) == ""


def test_module_hls_url_handles_none_and_malformed():
    assert get_module_hls_url(FakeRoom(module_config=None)) == ""
    assert get_module_hls_url(FakeRoom(module_config=["not-a-dict"])) == ""


def test_module_hls_url_strips_whitespace():
    room = FakeRoom(
        module_config=[
            {"type": "livestream.native", "config": {"hls_url": "  https://x/r.m3u8  "}}
        ]
    )
    assert get_module_hls_url(room) == "https://x/r.m3u8"


# -- schedule extraction -----------------------------------------------


def test_schedule_prefers_active():
    room = FakeRoom(
        schedules=[
            FakeSchedule("https://old/x.m3u8", start_time=1, active=False),
            FakeSchedule("https://live/x.m3u8", start_time=2, active=True),
        ]
    )
    assert get_schedule_hls_url(room) == "https://live/x.m3u8"


def test_schedule_falls_back_to_latest():
    room = FakeRoom(
        schedules=[
            FakeSchedule("https://a/x.m3u8", start_time=1, active=False),
            FakeSchedule("https://b/x.m3u8", start_time=5, active=False),
        ]
    )
    assert get_schedule_hls_url(room) == "https://b/x.m3u8"


def test_schedule_ignores_non_hls():
    room = FakeRoom(schedules=[FakeSchedule("https://yt", stream_type="youtube")])
    assert get_schedule_hls_url(room) == ""


# -- combined ----------------------------------------------------------


def test_get_room_hls_url_prefers_module_over_schedule():
    room = FakeRoom(
        module_config=[
            {"type": "livestream.native", "config": {"hls_url": "https://mod/x.m3u8"}}
        ],
        schedules=[FakeSchedule("https://sched/x.m3u8", active=True)],
    )
    assert get_room_hls_url(room) == "https://mod/x.m3u8"


def test_get_room_hls_url_falls_back_to_schedule():
    room = FakeRoom(
        module_config=[{"type": "chat.native", "config": {}}],
        schedules=[FakeSchedule("https://sched/x.m3u8", active=True)],
    )
    assert get_room_hls_url(room) == "https://sched/x.m3u8"


# -- start-session service ---------------------------------------------


class RecordingClient:
    def __init__(self):
        self.calls = []

    def create_session(self, source="url"):
        self.calls.append(("create_session", source))
        return "tenant-1"

    def configure(self, tenant_id, **kwargs):
        self.calls.append(("configure", tenant_id, kwargs))
        return None


def test_source_for_mapping():
    assert source_for("url") == "url"
    assert source_for("youtube") == "youtube"
    assert source_for("anything-else") == "url"


def test_start_stream_session_call_sequence():
    client = RecordingClient()
    tenant = start_stream_session(
        client,
        "https://x/r.m3u8",
        source_type="url",
        transcription_provider="whisper_local",
        translation_provider="nllb_local",
    )
    assert tenant == "tenant-1"
    assert client.calls[0] == ("create_session", "url")
    name, tenant_id, kwargs = client.calls[1]
    assert name == "configure"
    assert tenant_id == "tenant-1"
    assert kwargs["stream_url"] == "https://x/r.m3u8"
    assert kwargs["source_type"] == "url"
    assert kwargs["transcription"] == {"provider_name": "whisper_local"}
    assert kwargs["translation"] == {"provider_name": "nllb_local"}


def test_start_stream_session_omits_empty_providers():
    client = RecordingClient()
    start_stream_session(client, "https://x/r.m3u8")
    _, _, kwargs = client.calls[1]
    assert kwargs["transcription"] is None
    assert kwargs["translation"] is None


def test_start_stream_session_requires_hls_url():
    with pytest.raises(ValueError):
        start_stream_session(RecordingClient(), "")
