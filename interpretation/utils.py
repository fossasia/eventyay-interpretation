"""Resolve the stream URL Eventyay exposes for a room."""

from __future__ import annotations

import json

from django.utils.translation import gettext_lazy as _

MAX_BACKEND_CONFIG_KEYS = 32
MAX_BACKEND_CONFIG_BYTES = 8192
MAX_TARGET_LANGUAGES = 32
MAX_LANGUAGE_CODE_LEN = 20

NATIVE_LIVESTREAM = "livestream.native"
YOUTUBE_LIVESTREAM = "livestream.youtube"
IFRAME_LIVESTREAM = "livestream.iframe"

# configure ``stream_type``; SUSI rewrites ``.m3u8`` platform URLs to ``url`` itself.
SUSI_STREAM_TYPE = "platform"
# POST /session ``source`` (must stay in SUSI VALID_SOURCES).
SUSI_SESSION_SOURCE = "youtube"

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


def validate_backend_config(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError(_("backend_config must be an object."))
    if len(value) > MAX_BACKEND_CONFIG_KEYS:
        raise ValueError(_("backend_config has too many keys (max %(max)d).") % {"max": MAX_BACKEND_CONFIG_KEYS})
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(_("backend_config is not valid JSON.")) from exc
    if len(encoded) > MAX_BACKEND_CONFIG_BYTES:
        raise ValueError(_("backend_config is too large (max %(max)d bytes).") % {"max": MAX_BACKEND_CONFIG_BYTES})
    return dict(value)


def validate_target_language_codes(codes: list[str]) -> list[str]:
    if len(codes) > MAX_TARGET_LANGUAGES:
        raise ValueError(_("Too many target languages (max %(max)d).") % {"max": MAX_TARGET_LANGUAGES})
    for code in codes:
        if len(code) > MAX_LANGUAGE_CODE_LEN:
            raise ValueError(_("Language code too long: %(code)s") % {"code": code[:32]})
    return codes


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
