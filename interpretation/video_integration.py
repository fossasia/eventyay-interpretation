"""Inject plugin-owned language streams into video world room config."""

from __future__ import annotations

from .language_streams import attendee_language_streams
from .room_control import get_interpretation, plugin_enabled
from .settings import use_plugin_language_streams


def augment_room_config(room, room_config: dict) -> None:
    event = room.event
    if not plugin_enabled(event):
        return
    flag_on = use_plugin_language_streams(event)
    room_config["interpretation_use_plugin_streams"] = flag_on
    if not flag_on:
        return
    interpretation = get_interpretation(room)
    stored = interpretation.language_streams if interpretation else []
    room_config["interpretation_language_streams"] = attendee_language_streams(stored)


def install_video_integration() -> None:
    from eventyay.base.services import event as event_service

    if getattr(event_service, "_interpretation_video_patched", False):
        return

    original = event_service.get_room_config

    def get_room_config(room, permissions):
        config = original(room, permissions)
        augment_room_config(room, config)
        return config

    event_service.get_room_config = get_room_config
    event_service._interpretation_video_patched = True
