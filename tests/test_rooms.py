"""Tests for stream URL resolution and the start-session service."""

import pytest

from interpretation.services import (
    caption_coalesce_flush,
    caption_coalesce_ingest_frame,
    caption_coalesce_tick,
    caption_payload_for_language,
    start_stream_session,
)
from interpretation.utils import (
    SUSI_STREAM_TYPE,
    clear_module_interpretation,
    get_module_stream_url,
    get_room_stream_url,
    get_schedule_stream_url,
    room_settings_resume_path,
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
        self._items = list(items)

    def exclude(self, **kwargs):
        skip = set(kwargs.get("stream_type__in", ()))
        return FakeScheduleManager(
            [s for s in self._items if s.stream_type not in skip]
        )

    def __iter__(self):
        return iter(self._items)


# -- module_config extraction ------------------------------------------


def test_module_native_hls():
    room = FakeRoom(
        module_config=[
            {"type": "chat.native", "config": {}},
            {"type": "livestream.native", "config": {"hls_url": "https://x/r.m3u8"}},
        ]
    )
    assert get_module_stream_url(room) == "https://x/r.m3u8"


def test_module_youtube_id_normalized():
    room = FakeRoom(
        module_config=[
            {"type": "livestream.youtube", "config": {"ytid": "dQw4w9WgXcQ"}},
        ]
    )
    assert (
        get_module_stream_url(room)
        == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )


def test_module_youtube_url_passthrough():
    room = FakeRoom(
        module_config=[
            {
                "type": "livestream.youtube",
                "config": {"ytid": "https://youtu.be/abc123"},
            },
        ]
    )
    assert get_module_stream_url(room) == "https://youtu.be/abc123"


def test_module_absent_returns_empty():
    room = FakeRoom(module_config=[{"type": "call.bigbluebutton", "config": {}}])
    assert get_module_stream_url(room) == ""


def test_module_handles_none_and_malformed():
    assert get_module_stream_url(FakeRoom(module_config=None)) == ""
    assert get_module_stream_url(FakeRoom(module_config=["not-a-dict"])) == ""


# -- schedule extraction -----------------------------------------------


def test_schedule_prefers_active():
    room = FakeRoom(
        schedules=[
            FakeSchedule("https://old/x.m3u8", start_time=1, active=False),
            FakeSchedule("https://live/x.m3u8", start_time=2, active=True),
        ]
    )
    assert get_schedule_stream_url(room) == "https://live/x.m3u8"


def test_schedule_falls_back_to_latest():
    room = FakeRoom(
        schedules=[
            FakeSchedule("https://a/x.m3u8", start_time=1, active=False),
            FakeSchedule("https://b/x.m3u8", start_time=5, active=False),
        ]
    )
    assert get_schedule_stream_url(room) == "https://b/x.m3u8"


def test_schedule_includes_youtube():
    room = FakeRoom(
        schedules=[FakeSchedule("https://youtu.be/abc", stream_type="youtube")]
    )
    assert get_schedule_stream_url(room) == "https://youtu.be/abc"


def test_schedule_skips_iframe():
    room = FakeRoom(
        schedules=[FakeSchedule("https://embed/x", stream_type="iframe")]
    )
    assert get_schedule_stream_url(room) == ""


# -- combined ----------------------------------------------------------


def test_get_room_stream_url_prefers_module_over_schedule():
    room = FakeRoom(
        module_config=[
            {"type": "livestream.native", "config": {"hls_url": "https://mod/x.m3u8"}}
        ],
        schedules=[FakeSchedule("https://sched/x.m3u8", active=True)],
    )
    assert get_room_stream_url(room) == "https://mod/x.m3u8"


def test_get_room_stream_url_falls_back_to_schedule():
    room = FakeRoom(
        module_config=[{"type": "chat.native", "config": {}}],
        schedules=[FakeSchedule("https://sched/x.m3u8", active=True)],
    )
    assert get_room_stream_url(room) == "https://sched/x.m3u8"


# -- start-session service ---------------------------------------------


class RecordingClient:
    def __init__(self):
        self.calls = []

    def create_session(self, source="youtube"):
        self.calls.append(("create_session", source))
        return "tenant-1"

    def configure(self, tenant_id, **kwargs):
        self.calls.append(("configure", tenant_id, kwargs))
        return None


def test_start_stream_session_uses_susi_youtube_source():
    client = RecordingClient()
    tenant = start_stream_session(
        client,
        "https://vs-hls-push-ww-live.akamaized.net/x/master.m3u8",
        transcription_provider="whisper_local",
        translation_provider="nllb_local",
    )
    assert tenant == "tenant-1"
    assert client.calls[0] == ("create_session", SUSI_STREAM_TYPE)
    name, tenant_id, kwargs = client.calls[1]
    assert name == "configure"
    assert tenant_id == "tenant-1"
    assert kwargs["stream_url"].endswith("master.m3u8")
    assert kwargs["stream_type"] == SUSI_STREAM_TYPE
    assert kwargs["transcription"] == {"provider_name": "whisper_local"}
    assert kwargs["translation"] == {"provider_name": "nllb_local"}


def test_start_stream_session_omits_empty_providers():
    client = RecordingClient()
    start_stream_session(client, "https://www.youtube.com/watch?v=abc")
    _, _, kwargs = client.calls[1]
    assert kwargs["transcription"] is None
    assert kwargs["translation"] is None


def test_start_stream_session_requires_stream_url():
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
    assert native["config"]["hls_url"] == "https://x/r.m3u8"


def test_set_module_interpretation_on_youtube_module():
    room = FakeRoom(
        module_config=[
            {"type": "livestream.youtube", "config": {"ytid": "abc"}},
        ]
    )
    info = {"enabled": True, "languages": ["de"], "url": "https://host/captions/"}
    assert set_module_interpretation(room, info) is True
    assert room.module_config[0]["config"]["interpretation"] == info


def test_set_module_interpretation_without_stream_module_returns_false():
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


def test_room_settings_resume_path():
    assert room_settings_resume_path(42) == "video/admin/rooms/42"


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
    out = caption_payload_for_language(
        {"chunk_id": "3", "transcript": "hello", "translation": ""},
        target_requested=True,
        seen_translation=False,
    )
    assert out["translation"] == "hello"


def test_caption_payload_target_lagging_translation_is_held():
    out = caption_payload_for_language(
        {"chunk_id": "4", "transcript": "world", "translation": ""},
        target_requested=True,
        seen_translation=True,
    )
    assert out is None


def test_caption_payload_finalize_falls_back_to_transcript():
    out = caption_payload_for_language(
        {"chunk_id": "4", "transcript": "world", "translation": ""},
        target_requested=True,
        seen_translation=True,
        finalize=True,
    )
    assert out["translation"] == "world"


def test_caption_payload_empty_when_no_text():
    out = caption_payload_for_language(
        {"chunk_id": "3"}, target_requested=False, seen_translation=False
    )
    assert out is None


# -- caption partial coalesce ------------------------------------------


def _source_payload(data):
    return caption_payload_for_language(
        data, target_requested=False, seen_translation=False, finalize=True
    )


def test_caption_coalesce_emits_on_chunk_change_with_full_text():
    state: dict = {}
    partial = {"chunk_id": "1", "transcript": "hello", "translation": "hello"}
    final = {"chunk_id": "1", "transcript": "hello world", "translation": "hello world"}
    nxt = {"chunk_id": "2", "transcript": "next", "translation": "next"}
    assert caption_coalesce_ingest_frame(state, partial, _source_payload, now=0.0) == []
    assert caption_coalesce_ingest_frame(state, final, _source_payload, now=0.1) == []
    assert caption_coalesce_tick(state, _source_payload, now=0.2) is None
    out = caption_coalesce_ingest_frame(state, nxt, _source_payload, now=0.3)
    assert out == [final]


def test_caption_coalesce_emits_skipped_chunk_ids():
    state: dict = {}
    caption_coalesce_ingest_frame(
        state,
        {"chunk_id": 2, "transcript": "two", "translation": "two"},
        _source_payload,
        now=0.0,
    )
    out = caption_coalesce_ingest_frame(
        state,
        {"chunk_id": 5, "transcript": "five", "translation": "five"},
        _source_payload,
        now=0.1,
    )
    assert [item["chunk_id"] for item in out] == [2]


def test_caption_coalesce_tick_emits_trailing_chunk():
    state: dict = {}
    caption_coalesce_ingest_frame(
        state,
        {"chunk_id": "1", "transcript": "last line", "translation": "last line"},
        _source_payload,
        now=0.0,
    )
    assert caption_coalesce_tick(state, _source_payload, now=0.2) is None
    out = caption_coalesce_tick(state, _source_payload, now=0.7)
    assert out["transcript"] == "last line"
    assert caption_coalesce_tick(state, _source_payload, now=1.2) is None


def test_caption_coalesce_normalizes_chunk_id_types():
    state: dict = {}
    caption_coalesce_ingest_frame(
        state,
        {"chunk_id": 1, "transcript": "one", "translation": "one"},
        _source_payload,
        now=0.0,
    )
    out = caption_coalesce_ingest_frame(
        state,
        {"chunk_id": "2", "transcript": "two", "translation": "two"},
        _source_payload,
        now=0.1,
    )
    assert out[0]["chunk_id"] == 1
