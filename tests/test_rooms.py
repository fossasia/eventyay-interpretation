"""Tests for stream URL resolution and the start-session service."""

import pytest

from interpretation.services import start_stream_session
from interpretation.utils import (
    SUSI_SESSION_SOURCE,
    SUSI_STREAM_TYPE,
    get_module_stream_url,
    get_room_stream_url,
    get_schedule_stream_url,
    room_settings_resume_path,
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
        return FakeScheduleManager([s for s in self._items if s.stream_type not in skip])

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
    assert get_module_stream_url(room) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


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
    room = FakeRoom(schedules=[FakeSchedule("https://youtu.be/abc", stream_type="youtube")])
    assert get_schedule_stream_url(room) == "https://youtu.be/abc"


def test_schedule_skips_iframe():
    room = FakeRoom(schedules=[FakeSchedule("https://embed/x", stream_type="iframe")])
    assert get_schedule_stream_url(room) == ""


# -- combined ----------------------------------------------------------


def test_get_room_stream_url_prefers_module_over_schedule():
    room = FakeRoom(
        module_config=[{"type": "livestream.native", "config": {"hls_url": "https://mod/x.m3u8"}}],
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


def test_start_stream_session_uses_platform_stream_type():
    client = RecordingClient()
    tenant = start_stream_session(
        client,
        "https://vs-hls-push-ww-live.akamaized.net/x/master.m3u8",
        transcription_provider="faster_whisper",
        translation_provider="nllb_ctranslate2",
        source_language="en",
        target_languages=["de", "fr"],
    )
    assert tenant == "tenant-1"
    assert client.calls[0] == ("create_session", SUSI_SESSION_SOURCE)
    name, tenant_id, kwargs = client.calls[1]
    assert name == "configure"
    assert tenant_id == "tenant-1"
    assert kwargs["stream_url"].endswith("master.m3u8")
    assert kwargs["stream_type"] == SUSI_STREAM_TYPE
    assert kwargs["transcription"] == {"provider_name": "faster_whisper"}
    assert kwargs["translation"] == {
        "provider_name": "nllb_ctranslate2",
        "source_lang": "en",
        "target_lang": "de",
    }


def test_start_stream_session_defaults_empty_providers():
    client = RecordingClient()
    start_stream_session(client, "https://www.youtube.com/watch?v=abc")
    _, _, kwargs = client.calls[1]
    assert kwargs["transcription"] == {"provider_name": "faster_whisper"}
    assert kwargs["translation"] == {"provider_name": "nllb_ctranslate2"}


def test_start_stream_session_maps_legacy_provider_names():
    client = RecordingClient()
    start_stream_session(
        client,
        "https://www.youtube.com/watch?v=abc",
        transcription_provider="whisper_local",
        translation_provider="nllb_local",
    )
    _, _, kwargs = client.calls[1]
    assert kwargs["transcription"] == {"provider_name": "faster_whisper"}
    assert kwargs["translation"] == {"provider_name": "nllb_ctranslate2"}


def test_start_stream_session_requires_stream_url():
    with pytest.raises(ValueError):
        start_stream_session(RecordingClient(), "")


def test_start_stream_session_rolls_back_on_configure_failure():
    from interpretation.susi import SusiError

    class FailingClient(RecordingClient):
        def configure(self, tenant_id, **kwargs):
            raise SusiError("configure failed")

        def stop_session(self, tenant_id):
            self.calls.append(("stop_session", tenant_id))

    client = FailingClient()
    with pytest.raises(SusiError):
        start_stream_session(client, "https://www.youtube.com/watch?v=abc")
    assert client.calls[0] == ("create_session", SUSI_SESSION_SOURCE)
    assert ("stop_session", "tenant-1") in client.calls


def test_room_settings_resume_path():
    assert room_settings_resume_path(42) == "video/admin/rooms/42"
