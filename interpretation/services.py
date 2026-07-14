"""Orchestration helpers that drive the SUSI client for a room session."""

from __future__ import annotations

import time

from .utils import SUSI_STREAM_TYPE

# ponytail: emit on chunk rollover; tick only for trailing phrase
CAPTION_QUIET_FLUSH_SECONDS = 1.2


def _retry_pending_emits(state: dict, build_payload) -> list[dict]:
    pending = state.get("pending_emit") or []
    if not pending:
        return []
    out: list[dict] = []
    still_pending = []
    for chunk_id in pending:
        flushed = _emit_chunk(state, chunk_id, build_payload)
        if flushed:
            out.append(flushed)
        else:
            still_pending.append(chunk_id)
    state["pending_emit"] = still_pending
    return out


def _queue_pending_emit(state: dict, chunk_id) -> None:
    pending = state.setdefault("pending_emit", [])
    if chunk_id not in pending:
        pending.append(chunk_id)


def _provider_config(provider_name: str):
    return {"provider_name": provider_name} if provider_name else None


def caption_payload_for_language(
    data: dict, target_requested: bool, seen_translation: bool, *, finalize: bool = False
):
    """Build the SSE caption payload for one event, or ``None`` to skip it.

    Behaviour:
    - Source captions (no target language requested): always show the transcript.
    - Target language requested and a translation is present: show it.
    - Target language requested but translation missing:
        * if no translation has ever been produced for this stream, fall back to
          the source transcript so the box is never blank;
        * otherwise the translation is merely lagging for this chunk, so return
          ``None`` to hold the previous translated caption instead of flashing
          the source language.
    """
    transcript = data.get("transcript") or ""
    translation = data.get("translation") or ""
    chunk_id = data.get("chunk_id", "")

    if not target_requested:
        if not transcript:
            return None
        return {
            "chunk_id": chunk_id,
            "transcript": transcript,
            "translation": transcript,
        }

    if translation:
        return {
            "chunk_id": chunk_id,
            "transcript": transcript,
            "translation": translation,
        }

    if not seen_translation:
        if not transcript:
            return None
        return {
            "chunk_id": chunk_id,
            "transcript": transcript,
            "translation": transcript,
        }

    # Translation is expected but lagging for this chunk: hold while streaming.
    if finalize and transcript:
        return {
            "chunk_id": chunk_id,
            "transcript": transcript,
            "translation": transcript,
        }
    return None


def _normalize_chunk_id(value) -> int | str | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _emit_chunk(state: dict, chunk_id, build_payload) -> dict | None:
    emitted_chunks = state.setdefault("emitted_chunks", set())
    if chunk_id in emitted_chunks:
        return None
    data = state.get("frames", {}).get(chunk_id)
    if not data:
        return None
    payload = build_payload(data)
    if not payload:
        return None
    emitted_chunks.add(chunk_id)
    return payload


def caption_coalesce_flush(state: dict, build_payload) -> dict | None:
    """Force-emit the active chunk (stream end)."""
    chunk_id = state.get("chunk_id")
    if chunk_id is None:
        return None
    return _emit_chunk(state, chunk_id, build_payload)


def caption_coalesce_ingest_frame(
    state: dict,
    data: dict,
    build_payload,
    *,
    now: float | None = None,
) -> list[dict]:
    """Track every upstream frame; emit a chunk only once it is finished."""
    now = time.monotonic() if now is None else now
    chunk_id = _normalize_chunk_id(data.get("chunk_id"))
    if chunk_id is None:
        return []

    frames = state.setdefault("frames", {})
    frames[chunk_id] = data

    out: list[dict] = _retry_pending_emits(state, build_payload)
    prev_chunk = state.get("chunk_id")

    if prev_chunk is not None and chunk_id != prev_chunk:
        # ponytail: chunk_id is a SUSI timestamp, not 1..n; never range() between ids
        flushed = _emit_chunk(state, prev_chunk, build_payload)
        if flushed:
            out.append(flushed)
        else:
            _queue_pending_emit(state, prev_chunk)

    state["chunk_id"] = chunk_id
    state["last_partial_monotonic"] = now
    return out


def caption_coalesce_tick(
    state: dict,
    build_payload,
    *,
    now: float | None = None,
    quiet_flush_seconds: float = CAPTION_QUIET_FLUSH_SECONDS,
) -> dict | None:
    """Emit the last open chunk after the speaker pauses."""
    chunk_id = state.get("chunk_id")
    if chunk_id is None or chunk_id in state.get("emitted_chunks", set()):
        return None
    now = time.monotonic() if now is None else now
    last_partial = state.get("last_partial_monotonic", 0.0)
    if now - last_partial < quiet_flush_seconds:
        return None
    return _emit_chunk(state, chunk_id, build_payload)


def start_stream_session(
    client,
    stream_url: str,
    *,
    transcription_provider: str = "",
    translation_provider: str = "",
) -> str:
    """Create a SUSI session and configure it to ingest ``stream_url``.

    All Eventyay stream URLs are sent through SUSI's ``youtube`` source
    (``YouTubeSource``), which handles YouTube, Twitch, Vimeo, and HLS via
    yt-dlp / ffmpeg.

    Returns the SUSI tenant/session id. Raises ``SusiError`` on failure.
    """
    if not stream_url:
        raise ValueError("stream_url is required to start a session")

    tenant_id = client.create_session(source=SUSI_STREAM_TYPE)
    client.configure(
        tenant_id,
        stream_url=stream_url,
        stream_type=SUSI_STREAM_TYPE,
        transcription=_provider_config(transcription_provider),
        translation=_provider_config(translation_provider),
    )
    return tenant_id
