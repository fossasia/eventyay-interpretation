from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from eventyay.base.settings import settings_hierarkey
from eventyay.control.signals import nav_event_common

from .backends.susi_credentials import EVENT_SETTINGS_KEYS as SUSI_EVENT_SETTINGS_KEYS
from .backends.voxbento_credentials import EVENT_SETTINGS_KEYS as VOXBENTO_EVENT_SETTINGS_KEYS
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
