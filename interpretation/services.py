"""Orchestration helpers that drive the SUSI client for a room session."""

from __future__ import annotations

from .susi import SusiError
from .susi_providers import (
    resolve_transcription_provider,
    resolve_translation_provider,
)
from .utils import SUSI_SESSION_SOURCE, SUSI_STREAM_TYPE


def _provider_config(provider_name: str):
    resolved = resolve_transcription_provider(provider_name)
    return {"provider_name": resolved}


def _translation_config(
    provider_name: str,
    *,
    source_language: str = "",
    target_languages: list[str] | None = None,
):
    resolved = resolve_translation_provider(provider_name)
    cfg = {"provider_name": resolved}
    source = (source_language or "").strip()
    if source:
        cfg["source_lang"] = source
    targets = [(code or "").strip() for code in (target_languages or [])]
    targets = [code for code in targets if code]
    if targets:
        # ponytail: SUSI default target_lang; viewers override per SSE connection.
        cfg["target_lang"] = targets[0]
    return cfg


def start_stream_session(
    client,
    stream_url: str,
    *,
    transcription_provider: str = "",
    translation_provider: str = "",
    source_language: str = "",
    target_languages: list[str] | None = None,
) -> str:
    """Create a SUSI session and configure it to ingest ``stream_url``.

    Eventyay stream URLs use SUSI ``platform`` ingest (YouTube/Twitch/Vimeo/HLS).

    Returns the SUSI tenant/session id. Raises ``SusiError`` on failure.
    """
    if not stream_url:
        raise ValueError("stream_url is required to start a session")

    tenant_id = client.create_session(source=SUSI_SESSION_SOURCE)
    try:
        client.configure(
            tenant_id,
            stream_url=stream_url,
            stream_type=SUSI_STREAM_TYPE,
            transcription=_provider_config(transcription_provider),
            translation=_translation_config(
                translation_provider,
                source_language=source_language,
                target_languages=target_languages,
            ),
        )
    except Exception:
        try:
            client.stop_session(tenant_id)
        except SusiError:
            pass
        raise
    return tenant_id
