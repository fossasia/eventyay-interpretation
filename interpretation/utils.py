"""Helpers for resolving a room's stream configuration."""

from __future__ import annotations

NATIVE_LIVESTREAM_TYPE = "livestream.native"


def get_module_hls_url(room) -> str:
    """Return the HLS URL configured on the room's native livestream module.
    Returns an empty string when there is no native livestream module or no
    URL configured.
    """
    modules = room.module_config or []
    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("type") == NATIVE_LIVESTREAM_TYPE:
            config = module.get("config") or {}
            return (config.get("hls_url") or "").strip()
    return ""


def get_schedule_hls_url(room, at_time=None) -> str:
    """Return an HLS URL from the room's stream schedules.
    Prefers a schedule that is currently active; otherwise falls back to the
    most recent HLS schedule. Returns an empty string when none is found.
    """
    schedules = getattr(room, "stream_schedules", None)
    if schedules is None:
        return ""

    hls_schedules = schedules.filter(stream_type="hls")

    active = [s for s in hls_schedules if s.is_active(at_time)]
    if active:
        return (active[0].url or "").strip()

    latest = hls_schedules.order_by("-start_time").first()
    if latest:
        return (latest.url or "").strip()
    return ""


def get_room_hls_url(room, at_time=None) -> str:
    """Best-effort HLS URL for a room.
    Checks the native livestream module first (the persistent room stream),
    then falls back to the room's HLS stream schedules.
    """
    return get_module_hls_url(room) or get_schedule_hls_url(room, at_time)


def set_module_interpretation(room, info: dict) -> bool:
    """Write interpretation discovery info into the native livestream module.
    Returns True if a native livestream module was found and updated.
    """
    modules = room.module_config or []
    for module in modules:
        if isinstance(module, dict) and module.get("type") == NATIVE_LIVESTREAM_TYPE:
            config = module.setdefault("config", {})
            config["interpretation"] = info
            room.module_config = modules
            return True
    return False


def clear_module_interpretation(room) -> bool:
    """Remove interpretation discovery info from the native livestream module."""
    modules = room.module_config or []
    changed = False
    for module in modules:
        if isinstance(module, dict) and module.get("type") == NATIVE_LIVESTREAM_TYPE:
            config = module.get("config") or {}
            if "interpretation" in config:
                config.pop("interpretation", None)
                changed = True
    if changed:
        room.module_config = modules
    return changed
