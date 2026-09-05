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
    room_config["interpretation_use_plugin_streams"] = flag_on
    if not flag_on:
        return
    interpretation = get_interpretation(room)
    stored = interpretation.language_streams if interpretation else []
    room_config["interpretation_language_streams"] = attendee_language_streams(stored, event, room)


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
