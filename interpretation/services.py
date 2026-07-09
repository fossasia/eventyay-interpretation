"""Orchestration helpers that drive the SUSI client for a room session."""

from __future__ import annotations

from .utils import SUSI_STREAM_TYPE


def _provider_config(provider_name: str):
    return {"provider_name": provider_name} if provider_name else None


def caption_payload_for_language(
    data: dict, target_requested: bool, seen_translation: bool
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

    # Translation is expected but lagging for this chunk: hold the last caption.
    return None


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
