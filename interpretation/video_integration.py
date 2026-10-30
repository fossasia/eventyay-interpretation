"""Inject plugin-owned language streams into video world room config."""

from __future__ import annotations

from .language_streams import attendee_language_streams
from .room_control import get_interpretation, plugin_enabled
from .settings import use_plugin_language_streams


def augment_room_config(room, room_config: dict) -> None:
    event = getattr(room, "event", None)
    if event is None:
        return
    if not plugin_enabled(event):
        return
    flag_on = use_plugin_language_streams(event)
    if not flag_on:
        room_config["interpretation_use_plugin_streams"] = False
        return

    interpretation = get_interpretation(room)
    if not interpretation or not interpretation.room_enabled or interpretation.interpreter == "none":
        room_config["interpretation_use_plugin_streams"] = False
        return

    stored = interpretation.language_streams or []

    if interpretation.interpreter == "voxbento":
        from .backends.voxbento_credentials import VoxbentoError, get_voxbento_base_url

        try:
            base_url = get_voxbento_base_url(event)
        except (VoxbentoError, ValueError):
            base_url = None
        grant = getattr(event, "voxbento_oauth_grant", None)
        has_active_grant = bool(
            grant and not getattr(grant, "is_disconnected", False) and getattr(grant, "access_token", "")
        )
        if not (base_url and has_active_grant):
            if not any(s.get("language") != "Original" for s in stored):
                room_config["interpretation_use_plugin_streams"] = False
                return

    room_for_captions = room if interpretation.interpreter == "voxbento" else None
    streams = attendee_language_streams(stored, event, room_for_captions)

    has_alternative = any(s.get("language") != "Original" for s in streams)
    has_captions = any(s.get("caption_ws_url") for s in streams)
    is_active = has_alternative or has_captions
    room_config["interpretation_use_plugin_streams"] = is_active
    if is_active:
        room_config["interpretation_language_streams"] = streams


def install_video_integration() -> None:
    """Patch attendee + admin room config builders (no Eventyay core changes)."""
    from eventyay.base.services import event as event_service
    from eventyay.features.live.modules import room as room_module

    if getattr(install_video_integration, "_patched", False):
        return

    original_get_room_config = event_service.get_room_config

    def get_room_config(room, permissions, **kwargs):
        config = original_get_room_config(room, permissions, **kwargs)
        augment_room_config(room, config)
        return config

    original_serialize_room_config = room_module.serialize_room_config

    def serialize_room_config(room_or_rooms, many=False):
        data = original_serialize_room_config(room_or_rooms, many=many)
        if many:
            for room, item in zip(room_or_rooms, data, strict=True):
                augment_room_config(room, item)
        else:
            augment_room_config(room_or_rooms, data)
        return data

    # Attendee world.config uses get_room_config; video admin uses
    # serialize_room_config.
    event_service.get_room_config = get_room_config
    room_module.serialize_room_config = serialize_room_config
    install_video_integration._patched = True
