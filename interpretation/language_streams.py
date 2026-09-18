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
STREAM_TYPE_HUMAN = "human"
STREAM_TYPE_AI = "ai"
STREAM_TYPES = (STREAM_TYPE_HUMAN, STREAM_TYPE_AI)

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


def is_usable_stream_entry(entry: dict | None, allow_blank: bool = False) -> bool:
    if not entry or not (entry.get("language") or "").strip():
        return False
    language = entry["language"].strip()
    if language == ORIGINAL_LANGUAGE:
        return True
    source = entry.get("youtube_id") or entry.get("audio_source") or ""
    if allow_blank and not source:
        return True
    return bool(normalize_audio_source(source))


def stream_type_of(entry: dict | None) -> str:
    """Return ``"ai"`` for VoxBento TTS entries and ``"human"`` for everything else."""
    raw = (entry or {}).get("stream_type")
    if not isinstance(raw, str):
        return STREAM_TYPE_HUMAN
    return STREAM_TYPE_AI if raw.strip().lower() == STREAM_TYPE_AI else STREAM_TYPE_HUMAN


def normalize_stream_entry(entry: dict) -> dict:
    language = (entry.get("language") or "").strip()
    stream_type = stream_type_of(entry)
    if stream_type == STREAM_TYPE_AI:
        # AI audio is served over VoxBento's TTS WebSocket, built per request.
        normalized_source = ""
    else:
        raw_source = (entry.get("youtube_id") or entry.get("audio_source") or "").strip()
        normalized_source = normalize_audio_source(raw_source) or ""
    return {
        "language": language,
        "youtube_id": normalized_source,
        "use_video": bool(entry.get("use_video")) and stream_type == STREAM_TYPE_HUMAN,
        "stream_type": stream_type,
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
        raw_stream_type = raw.get("stream_type") or STREAM_TYPE_HUMAN
        if not isinstance(raw_stream_type, str) or raw_stream_type.strip().lower() not in STREAM_TYPES:
            raise ValidationError(_("Stream type must be either human or ai."))
        entry = normalize_stream_entry(raw)
        language = entry["language"]
        if not language:
            continue
        if language == ORIGINAL_LANGUAGE:
            raise ValidationError(_("Do not store Original in language streams."))
        if language in seen_languages:
            raise ValidationError(_("Duplicate language: %(language)s") % {"language": language})
        if not entry["youtube_id"]:
            # Allow blank youtube_id so VoxBento can auto-populate it
            pass
        seen_languages.add(language)
        cleaned.append(entry)

    if len(cleaned) > MAX_LANGUAGE_STREAMS:
        raise ValidationError(_("At most %(max)s language streams are allowed.") % {"max": MAX_LANGUAGE_STREAMS})
    return cleaned


def attendee_language_streams(stored_streams: list | None, event=None, room=None) -> list[dict]:
    """Dropdown payload for the video room, always including Original."""
    allow_blank = False
    base_url = None
    grant = None
    has_active_grant = False
    if event and room:
        from .backends.voxbento_credentials import VoxbentoError, get_voxbento_base_url

        try:
            base_url = get_voxbento_base_url(event)
        except VoxbentoError:
            base_url = None

        grant = getattr(event, "voxbento_oauth_grant", None)
        has_active_grant = (
            grant and not getattr(grant, "is_disconnected", False) and bool(getattr(grant, "access_token", ""))
        )
        if base_url and has_active_grant:
            allow_blank = True

    streams = []
    for entry in stored_streams or []:
        if not is_usable_stream_entry(entry, allow_blank=allow_blank):
            continue
        source = entry.get("youtube_id") or entry.get("audio_source") or ""
        if not has_active_grant and base_url and source.startswith(base_url):
            continue
        streams.append(entry)
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

    # Inject VoxBento WebSockets if event and room are provided
    if event and room:
        from .language_map import language_code_for_name

        has_active_grant = (
            grant and not getattr(grant, "is_disconnected", False) and bool(getattr(grant, "access_token", ""))
        )
        if base_url and has_active_grant:
            parsed = urlparse(base_url)
            scheme = "wss" if parsed.scheme == "https" else "ws"
            ws_base = f"{scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

            # Use the VoxBento room ID if we have it saved, otherwise fallback to Eventyay's room ID
            v_room_id = str(room.id)
            voxbento_room_id = None
            if hasattr(room, "interpretation") and room.interpretation.backend_session_id:
                voxbento_room_id = room.interpretation.backend_session_id
                v_room_id = voxbento_room_id
            floor_booth_id = f"{event.slug}-{v_room_id}-floor"

            for entry in normalized:
                if entry["language"] == ORIGINAL_LANGUAGE:
                    entry["caption_ws_url"] = f"{ws_base}/ws/captions/{floor_booth_id}"
                else:
                    lang_code = language_code_for_name(entry["language"])
                    if lang_code:
                        entry["language_code"] = lang_code
                        booth_id = f"{event.slug}-{v_room_id}-{lang_code}"
                        entry["caption_ws_url"] = f"{ws_base}/ws/captions/{booth_id}"
                        if entry.get("stream_type") == STREAM_TYPE_AI and voxbento_room_id:
                            # VoxBento broadcasts floor TTS keyed by its own room ID,
                            # the target language and the floor booth that produced it.
                            entry["tts_ws_url"] = f"{ws_base}/ws/tts/{voxbento_room_id}/{lang_code}/{floor_booth_id}"

    # An AI entry is only playable once VoxBento has given us a TTS endpoint.
    return [entry for entry in normalized if entry.get("stream_type") != STREAM_TYPE_AI or entry.get("tts_ws_url")]
