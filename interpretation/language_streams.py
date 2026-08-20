"""Attendee language stream list (YouTube ID or WHEP URL per language).

Mirrors eventyay/webapp/video/src/lib/validators.js and the legacy
``languageUrls`` room-module shape so MediaSource playback keeps working.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ORIGINAL_LANGUAGE = "Original"
MAX_LANGUAGE_STREAMS = 20

_YOUTUBE_ID_RE = re.compile(r"(?:youtu\.be/|v=|/embed/|/shorts/|/live/|/v/)([0-9A-Za-z_-]{11})")


def normalize_youtube_video_id(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if len(raw) == 11 and raw.replace("-", "").replace("_", "").isalnum():
        return raw
    match = _YOUTUBE_ID_RE.search(raw)
    return match.group(1) if match else None


def normalize_audio_source(audio_source: str) -> str | None:
    """Return YouTube id or absolute URL (WHEP/HLS/etc.), like video validators."""
    if not audio_source:
        return None
    youtube_id = normalize_youtube_video_id(audio_source)
    if youtube_id:
        return youtube_id
    try:
        parsed = urlparse(audio_source.strip())
    except ValueError:
        return None
    if parsed.scheme in {"http", "https", "wss", "ws"} and parsed.netloc:
        return audio_source.strip()
    return None


def is_whep_or_url_source(audio_source: str) -> bool:
    normalized = normalize_audio_source(audio_source)
    if not normalized:
        return False
    return normalize_youtube_video_id(normalized) is None


def is_usable_stream_entry(entry: dict | None) -> bool:
    if not entry or not (entry.get("language") or "").strip():
        return False
    language = entry["language"].strip()
    if language == ORIGINAL_LANGUAGE:
        return True
    source = entry.get("youtube_id") or entry.get("audio_source") or ""
    return bool(normalize_audio_source(source))


def normalize_stream_entry(entry: dict) -> dict:
    language = (entry.get("language") or "").strip()
    raw_source = (entry.get("youtube_id") or entry.get("audio_source") or "").strip()
    normalized_source = normalize_audio_source(raw_source) or ""
    return {
        "language": language,
        "youtube_id": normalized_source,
        "use_video": bool(entry.get("use_video")),
    }


def validate_language_streams(streams) -> list[dict]:
    if streams in (None, ""):
        return []
    if not isinstance(streams, list):
        raise ValidationError(_("Language streams must be a list."))

    cleaned: list[dict] = []
    seen_languages: set[str] = set()
    for raw in streams:
        if not isinstance(raw, dict):
            raise ValidationError(_("Each language stream must be an object."))
        entry = normalize_stream_entry(raw)
        language = entry["language"]
        if not language:
            continue
        if language == ORIGINAL_LANGUAGE:
            raise ValidationError(_("Do not store Original in language streams."))
        if language in seen_languages:
            raise ValidationError(_("Duplicate language: %(language)s") % {"language": language})
        if not entry["youtube_id"]:
            raise ValidationError(_("Each language needs a YouTube ID or WHEP URL."))
        seen_languages.add(language)
        cleaned.append(entry)

    if len(cleaned) > MAX_LANGUAGE_STREAMS:
        raise ValidationError(_("At most %(max)s language streams are allowed.") % {"max": MAX_LANGUAGE_STREAMS})
    return cleaned


def attendee_language_streams(stored_streams: list | None) -> list[dict]:
    """Dropdown payload for the video room, always including Original."""
    streams = [entry for entry in (stored_streams or []) if is_usable_stream_entry(entry)]
    normalized = [normalize_stream_entry(entry) for entry in streams]
    if not any(entry["language"] == ORIGINAL_LANGUAGE for entry in normalized):
        normalized.insert(
            0,
            {
                "language": ORIGINAL_LANGUAGE,
                "youtube_id": "",
                "use_video": False,
            },
        )
    return normalized
