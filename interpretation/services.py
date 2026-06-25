"""Orchestration helpers that drive the SUSI client for a room session.

These functions are intentionally free of Django/ORM and request state so they
can be unit-tested directly with a mocked client.
"""

from __future__ import annotations


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


def source_for(source_type: str) -> str:
    """Map a stream source type to a SUSI session source alias."""
    return "youtube" if source_type == "youtube" else "url"


def start_stream_session(
    client,
    hls_url: str,
    *,
    source_type: str = "url",
    transcription_provider: str = "",
    translation_provider: str = "",
) -> str:
    """Create a SUSI session and configure it to ingest ``hls_url``.

    Returns the SUSI tenant/session id. Raises ``SusiError`` (from the client)
    on failure.
    """
    if not hls_url:
        raise ValueError("hls_url is required to start a session")

    tenant_id = client.create_session(source=source_for(source_type))
    client.configure(
        tenant_id,
        stream_url=hls_url,
        source_type=source_type,
        transcription=_provider_config(transcription_provider),
        translation=_provider_config(translation_provider),
    )
    return tenant_id
