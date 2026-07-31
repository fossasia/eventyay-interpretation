"""Resolve the stream URL Eventyay exposes for a room."""

from __future__ import annotations

NATIVE_LIVESTREAM = "livestream.native"
YOUTUBE_LIVESTREAM = "livestream.youtube"
IFRAME_LIVESTREAM = "livestream.iframe"

# SUSI ``YouTubeSource``: yt-dlp for platform URLs, ffmpeg for direct ``.m3u8``.
SUSI_STREAM_TYPE = "youtube"

# Embed-only schedules have no direct audio URL for SUSI to ingest.
_SKIP_SCHEDULE_TYPES = frozenset({"iframe"})


def _strip(url: str) -> str:
    return (url or "").strip()


def _youtube_url(value: str) -> str:
    value = _strip(value)
    if not value:
        return ""
    if "://" in value:
        return value
    return f"https://www.youtube.com/watch?v={value}"


def _url_from_module(module: dict) -> str:
    module_type = module.get("type")
    config = module.get("config") or {}
    if module_type == NATIVE_LIVESTREAM:
        return _strip(config.get("hls_url"))
    if module_type == YOUTUBE_LIVESTREAM:
        return _youtube_url(config.get("ytid", ""))
    if module_type == IFRAME_LIVESTREAM:
        return _strip(config.get("url"))
    return ""


def get_module_stream_url(room) -> str:
    """URL from the room's stage module (native HLS, YouTube, or iframe player)."""
    for module in room.module_config or []:
        if not isinstance(module, dict):
            continue
        url = _url_from_module(module)
        if url:
            return url
    return ""


def _schedules(room):
    schedules = getattr(room, "stream_schedules", None)
    if schedules is None:
        return None
    if hasattr(schedules, "exclude"):
        return schedules.exclude(stream_type__in=_SKIP_SCHEDULE_TYPES)
    return schedules


def get_schedule_stream_url(room, at_time=None) -> str:
    """URL from timed stream schedules (YouTube, Vimeo, HLS, native, …)."""
    schedules = _schedules(room)
    if schedules is None:
        return ""

    items = list(schedules)
    active = [s for s in items if s.is_active(at_time) and _strip(s.url)]
    if active:
        return _strip(active[0].url)

    dated = [s for s in items if _strip(s.url)]
    if not dated:
        return ""
    latest = max(dated, key=lambda s: s.start_time)
    return _strip(latest.url)


def get_room_stream_url(room, at_time=None) -> str:
    """Best stream URL for a room: stage module first, then stream schedule."""
    return get_module_stream_url(room) or get_schedule_stream_url(room, at_time)


def interpretation_dashboard_url(organizer_slug: str, event_slug: str) -> str:
    from django.urls import reverse

    return reverse(
        "plugins:interpretation:dashboard",
        kwargs={"organizer": organizer_slug, "event": event_slug},
    )


def room_settings_resume_path(room_id: int) -> str:
    return f"video/admin/rooms/{room_id}"


def normalize_target_languages(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value
    elif isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            if len(value) == 1 and "," in value[0]:
                raw = value[0]
            else:
                codes = []
                seen = set()
                for code in value:
                    code = (code or "").strip()
                    if code and code not in seen:
                        seen.add(code)
                        codes.append(code)
                return codes
        raw = ",".join(str(item) for item in value)
    else:
        raw = str(value)
    codes = []
    seen = set()
    for code in raw.split(","):
        code = code.strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def room_settings_url(organizer_slug: str, event_slug: str, room_id: int) -> str:
    """Commons link that opens the video room editor for interpretation settings."""
    from django.urls import reverse

    base = reverse(
        "eventyay_common:event.create_access_to_video",
        kwargs={"organizer": organizer_slug, "event": event_slug},
    )
    return f"{base}?resume_path={room_settings_resume_path(room_id)}"
