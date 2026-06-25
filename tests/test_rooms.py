"""Tests for the HLS extraction helper and the start-session service."""

import pytest

from interpretation.services import (
    caption_payload_for_language,
    source_for,
    start_stream_session,
)
from interpretation.utils import (
    clear_module_interpretation,
    get_module_hls_url,
    get_room_hls_url,
    get_schedule_hls_url,
    set_module_interpretation,
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


# -- module_config interpretation discovery ----------------------------


def test_set_module_interpretation_writes_into_native_livestream():
    room = FakeRoom(
        module_config=[
            {"type": "chat.native", "config": {}},
            {"type": "livestream.native", "config": {"hls_url": "https://x/r.m3u8"}},
        ]
    )
    info = {"enabled": True, "languages": ["de"], "url": "https://host/captions/"}
    assert set_module_interpretation(room, info) is True
    native = [m for m in room.module_config if m["type"] == "livestream.native"][0]
    assert native["config"]["interpretation"] == info
    # Existing config (hls_url) is preserved.
    assert native["config"]["hls_url"] == "https://x/r.m3u8"


def test_set_module_interpretation_without_native_returns_false():
    room = FakeRoom(module_config=[{"type": "chat.native", "config": {}}])
    assert set_module_interpretation(room, {"enabled": True}) is False


def test_clear_module_interpretation_removes_info():
    room = FakeRoom(
        module_config=[
            {
                "type": "livestream.native",
                "config": {
                    "hls_url": "https://x/r.m3u8",
                    "interpretation": {"enabled": True},
                },
            }
        ]
    )
    assert clear_module_interpretation(room) is True
    native = room.module_config[0]
    assert "interpretation" not in native["config"]
    assert native["config"]["hls_url"] == "https://x/r.m3u8"


def test_clear_module_interpretation_noop_when_absent():
    room = FakeRoom(
        module_config=[
            {"type": "livestream.native", "config": {"hls_url": "https://x/r.m3u8"}}
        ]
    )
    assert clear_module_interpretation(room) is False


# -- translation provider threading ------------------------------------


def test_start_stream_session_configures_translation_provider():
    client = RecordingClient()
    start_stream_session(
        client,
        "https://x/r.m3u8",
        translation_provider="nllb_local",
    )
    _, _, kwargs = client.calls[1]
    assert kwargs["translation"] == {"provider_name": "nllb_local"}


# -- caption payload fallback ------------------------------------------


def test_caption_payload_source_mode_shows_transcript():
    out = caption_payload_for_language(
        {"chunk_id": "3", "transcript": "hello"},
        target_requested=False,
        seen_translation=False,
    )
    assert out == {"chunk_id": "3", "transcript": "hello", "translation": "hello"}


def test_caption_payload_target_with_translation_shows_translation():
    out = caption_payload_for_language(
        {"chunk_id": "3", "transcript": "hello", "translation": "hallo"},
        target_requested=True,
        seen_translation=True,
    )
    assert out["translation"] == "hallo"


def test_caption_payload_target_no_translation_yet_falls_back_to_source():
    # Translation never produced -> show source so the box is not blank.
    out = caption_payload_for_language(
        {"chunk_id": "3", "transcript": "hello", "translation": ""},
        target_requested=True,
        seen_translation=False,
    )
    assert out["translation"] == "hello"


def test_caption_payload_target_lagging_translation_is_held():
    # Translation seen before but lagging for this chunk -> skip, so the
    # previous translated caption is held instead of flashing the source.
    out = caption_payload_for_language(
        {"chunk_id": "4", "transcript": "world", "translation": ""},
        target_requested=True,
        seen_translation=True,
    )
    assert out is None


def test_caption_payload_empty_when_no_text():
    out = caption_payload_for_language(
        {"chunk_id": "3"}, target_requested=False, seen_translation=False
    )
    assert out is None
