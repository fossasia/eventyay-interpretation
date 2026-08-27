from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from eventyay.base.settings import settings_hierarkey
from eventyay.control.signals import nav_event_common

from .backends.susi_credentials import EVENT_SETTINGS_KEYS as SUSI_EVENT_SETTINGS_KEYS
from .backends.voxbento_credentials import (
    EVENT_SETTINGS_KEYS as VOXBENTO_EVENT_SETTINGS_KEYS,
)
from .settings import SETTING_IS_ENABLED, SETTING_USE_PLUGIN_STREAMS, is_interpretation_enabled

PLUGIN_MODULE = "interpretation"

settings_hierarkey.add_default(SETTING_IS_ENABLED, True, bool)
settings_hierarkey.add_default(SETTING_USE_PLUGIN_STREAMS, False, bool)
for _key in SUSI_EVENT_SETTINGS_KEYS:
    settings_hierarkey.add_default(_key, "", str)
for _key in VOXBENTO_EVENT_SETTINGS_KEYS:
    settings_hierarkey.add_default(_key, "", str)


@receiver(nav_event_common, dispatch_uid="interpretation_nav_event_common")
def navbar_entry_common(sender, request=None, **kwargs):
    if not request.user.has_event_permission(
        request.organizer,
        request.event,
        "can_change_event_settings",
        request=request,
    ):
        return []

    url = resolve(request.path_info)
    return [
        {
            "label": _("Interpretation"),
            "url": reverse(
                "plugins:interpretation:dashboard",
                kwargs={
                    "event": request.event.slug,
                    "organizer": request.event.organizer.slug,
                },
            ),
            "active": url.namespace == "plugins:interpretation",
            "icon": "language",
        }
    ]


from collections import OrderedDict

from django import forms
from eventyay.base.forms import SecretKeySettingsField
from eventyay.base.signals import register_global_settings


@receiver(register_global_settings, dispatch_uid="interpretation_global_settings")
def register_voxbento_global_settings(sender, **kwargs):
    return OrderedDict(
        [
            (
                "voxbento_base_url",
                forms.URLField(
                    label=_("VoxBento Base URL"),
                    required=False,
                    help_text=_("The base URL of your VoxBento deployment (e.g. https://voxbento.example.com)."),
                    widget=forms.URLInput(attrs={"placeholder": "https://voxbento.example.com"}),
                ),
            ),
            (
                "voxbento_client_id",
                forms.CharField(
                    label=_("VoxBento Client ID"),
                    required=False,
                    help_text=_("The OAuth Client ID obtained from the VoxBento Developer Dashboard."),
                ),
            ),
            (
                "voxbento_client_secret",
                SecretKeySettingsField(
                    label=_("VoxBento Client Secret"),
                    required=False,
                    help_text=_("The OAuth Client Secret obtained from the VoxBento Developer Dashboard."),
                ),
            ),
        ]
    )


import inspect
import threading

from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from eventyay.base.models import Room
from eventyay.base.services.event import notify_event_change
from rest_framework.exceptions import ValidationError

from .models import RoomInterpretation
from .tasks import ActiveSessionConflict, _do_sync_single_room_to_voxbento, sync_single_room_to_voxbento


def _raise_appropriate_exception(msg: str, event_id: int = None):
    short_msg = msg or "Booth is currently live. Refresh to restore."

    if event_id is not None:
        threading.Timer(1.0, lambda: async_to_sync(notify_event_change)(event_id)).start()

    try:
        from eventyay.features.live.exceptions import ConsumerException

        for frame_record in inspect.stack():
            if "channels/db.py" in frame_record.filename:
                raise ConsumerException(short_msg, short_msg)
    except ImportError:
        pass

    raise ValidationError({"module_config": [short_msg]})


@receiver(post_save, sender=RoomInterpretation, dispatch_uid="interpretation_room_interp_post_save")
def room_interpretation_post_save(sender, instance, created, **kwargs):
    if PLUGIN_MODULE not in instance.room.event.get_plugins():
        return
    if not is_interpretation_enabled(instance.room.event):
        return

    from interpretation.backends.voxbento_oauth import VoxbentoReauthorizationRequired

    needs_retry = False
    try:
        needs_retry = _do_sync_single_room_to_voxbento(
            instance.room.id,
            instance.room.event_id,
            "upsert",
            room_instance=instance.room,
            old_module_config=None,  # RoomInterpretation changes are additive; no removal guard needed
        )
    except ActiveSessionConflict as e:
        _raise_appropriate_exception(str(e), instance.room.event_id)
    except VoxbentoReauthorizationRequired:
        _raise_appropriate_exception(
            "VoxBento authorization expired. Please reconnect your account in settings.", instance.room.event_id
        )

    if needs_retry:
        transaction.on_commit(
            lambda: sync_single_room_to_voxbento.delay(instance.room.id, instance.room.event_id, "upsert")
        )


@receiver(pre_save, sender=Room, dispatch_uid="interpretation_room_pre_save")
def room_pre_save(sender, instance, **kwargs):
    if PLUGIN_MODULE not in instance.event.get_plugins():
        return
    if not is_interpretation_enabled(instance.event):
        return

    # Fetch the CURRENT (pre-save) DB state so we can detect which languages
    # are being removed. For new rooms (no PK yet), there is no old state.
    old_module_config = None
    if instance.pk:
        try:
            old_module_config = Room.objects.filter(pk=instance.pk).values_list("module_config", flat=True).first()
        except Exception:
            old_module_config = None

    from interpretation.backends.voxbento_oauth import VoxbentoReauthorizationRequired

    needs_retry = False
    try:
        needs_retry = _do_sync_single_room_to_voxbento(
            instance.id,
            instance.event_id,
            "upsert",
            room_instance=instance,
            old_module_config=old_module_config,
        )
    except ActiveSessionConflict as e:
        _raise_appropriate_exception(str(e), instance.event_id)
    except VoxbentoReauthorizationRequired:
        _raise_appropriate_exception(
            "VoxBento authorization expired. Please reconnect your account in settings.", instance.event_id
        )

    if needs_retry:
        transaction.on_commit(lambda: sync_single_room_to_voxbento.delay(instance.id, instance.event_id, "upsert"))


@receiver(post_delete, sender=Room, dispatch_uid="interpretation_room_post_delete")
def room_post_delete(sender, instance, **kwargs):
    if PLUGIN_MODULE not in instance.event.get_plugins():
        return

    room_id = instance.id
    event_id = instance.event_id
    transaction.on_commit(lambda: sync_single_room_to_voxbento.delay(room_id, event_id, "delete"))
