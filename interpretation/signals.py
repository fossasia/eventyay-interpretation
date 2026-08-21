from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from eventyay.base.settings import settings_hierarkey
from eventyay.control.signals import nav_event_common

from .backends.susi_credentials import EVENT_SETTINGS_KEYS as SUSI_EVENT_SETTINGS_KEYS
from .backends.voxbento_credentials import (
    EVENT_SETTINGS_KEYS as VOXBENTO_EVENT_SETTINGS_KEYS,
)
from .settings import SETTING_IS_ENABLED, SETTING_USE_PLUGIN_STREAMS

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


from django.db import transaction
from django.db.models.signals import post_delete, post_save
from eventyay.base.models import Room

from .models import RoomInterpretation
from .tasks import sync_single_room_to_voxbento


@receiver(post_save, sender=RoomInterpretation, dispatch_uid="interpretation_room_interp_post_save")
def room_interpretation_post_save(sender, instance, created, **kwargs):
    if PLUGIN_MODULE not in instance.room.event.get_plugins():
        return

    def _sync():
        sync_single_room_to_voxbento.delay(instance.room.id, instance.room.event_id, "upsert")

    transaction.on_commit(_sync)


@receiver(post_save, sender=Room, dispatch_uid="interpretation_room_post_save")
def room_post_save(sender, instance, created, **kwargs):
    if PLUGIN_MODULE not in instance.event.get_plugins():
        return

    transaction.on_commit(lambda: sync_single_room_to_voxbento.delay(instance.id, instance.event_id, "upsert"))


@receiver(post_delete, sender=Room, dispatch_uid="interpretation_room_post_delete")
def room_post_delete(sender, instance, **kwargs):
    if PLUGIN_MODULE not in instance.event.get_plugins():
        return

    room_id = instance.id
    event_id = instance.event_id
    transaction.on_commit(lambda: sync_single_room_to_voxbento.delay(room_id, event_id, "delete"))
