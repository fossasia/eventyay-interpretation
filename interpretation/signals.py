from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from eventyay.base.settings import settings_hierarkey
from eventyay.control.signals import nav_event_common

from .interpreter_credentials import (
    LEGACY_SUSI_AUTH_TOKEN,
    LEGACY_SUSI_BASE_URL,
    LEGACY_SUSI_EMAIL,
    LEGACY_SUSI_NAME,
    SETTING_SUSI_ACCOUNT_EMAIL,
    SETTING_SUSI_ACCOUNT_NAME,
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_BASE_URL,
)
from .settings import SETTING_IS_ENABLED

PLUGIN_MODULE = "interpretation"

settings_hierarkey.add_default(SETTING_IS_ENABLED, True, bool)
for _key in (
    SETTING_SUSI_BASE_URL,
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_ACCOUNT_EMAIL,
    SETTING_SUSI_ACCOUNT_NAME,
    LEGACY_SUSI_BASE_URL,
    LEGACY_SUSI_AUTH_TOKEN,
    LEGACY_SUSI_EMAIL,
    LEGACY_SUSI_NAME,
):
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
