"""SSE proxy from SUSI to the organizer caption preview."""

from __future__ import annotations

import asyncio
import json
import queue
import threading

from .susi import SusiError

# ponytail: padding forces first TCP flush through buffered ASGI/proxies.
_FLUSH_PAD = b": " + (b"-" * 8192) + b"\n\n"
_HEARTBEAT = b": heartbeat\n\n"
_CONNECTED = b'data: {"status":"connected"}\n\n'
_HEARTBEAT_SECS = 25.0


def _relay_upstream(client, tenant_id: str, put) -> None:
    try:
        for chunk in client.iter_caption_stream(tenant_id):
            if chunk:
                put(chunk)
    except SusiError as exc:
        payload = json.dumps({"status": "error", "message": str(exc)})
        put(f"data: {payload}\n\n".encode())


def stream_susi_captions_sync(client, tenant_id: str):
    """Yield SUSI SSE bytes; flush open comment first, then relay in a thread."""
    yield b": stream-open\n\n"
    yield _FLUSH_PAD
    yield _CONNECTED

    out: queue.Queue = queue.Queue()
    done = object()

    def put(item) -> None:
        out.put(item)

    def upstream() -> None:
        _relay_upstream(client, tenant_id, put)
        out.put(done)

    threading.Thread(target=upstream, daemon=True).start()

    while True:
        try:
            chunk = out.get(timeout=_HEARTBEAT_SECS)
        except queue.Empty:
            yield _HEARTBEAT
            continue
        if chunk is done:
            break
        yield chunk


async def stream_susi_captions_async(client, tenant_id: str):
    """Async SSE relay for Daphne — flush headers before blocking SUSI read."""
    yield b": stream-open\n\n"
    yield _FLUSH_PAD
    yield _CONNECTED

    out: asyncio.Queue = asyncio.Queue()
    done = object()
    loop = asyncio.get_running_loop()

    def put(item) -> None:
        loop.call_soon_threadsafe(out.put_nowait, item)

    def upstream() -> None:
        _relay_upstream(client, tenant_id, put)
        put(done)

    threading.Thread(target=upstream, daemon=True).start()

    while True:
        try:
            chunk = await asyncio.wait_for(out.get(), timeout=_HEARTBEAT_SECS)
        except TimeoutError:
            yield _HEARTBEAT
            continue
        if chunk is done:
            break
        yield chunk
