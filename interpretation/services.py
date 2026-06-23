"""Orchestration helpers that drive the SUSI client for a room session.

These functions are intentionally free of Django/ORM and request state so they
can be unit-tested directly with a mocked client.
"""

from __future__ import annotations


def _provider_config(provider_name: str):
    return {"provider_name": provider_name} if provider_name else None


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
