"""Orchestration helpers that drive the SUSI client for a room session."""

from __future__ import annotations

from .utils import SUSI_STREAM_TYPE


def _provider_config(provider_name: str):
    return {"provider_name": provider_name} if provider_name else None


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
