from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _
from eventyay.base.settings import settings_hierarkey
from eventyay.control.signals import nav_event_common, video_admin_event_forms

from .forms import InterpretationAdminForm
from .settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    SETTING_SUSI_EMAIL,
    SETTING_SUSI_NAME,
)

PLUGIN_MODULE = "interpretation"

settings_hierarkey.add_default(SETTING_BASE_URL, "", str)
settings_hierarkey.add_default(SETTING_AUTH_TOKEN, "", str)
settings_hierarkey.add_default(SETTING_SUSI_EMAIL, "", str)
settings_hierarkey.add_default(SETTING_SUSI_NAME, "", str)
settings_hierarkey.add_default(SETTING_IS_ENABLED, False, bool)


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


@receiver(video_admin_event_forms, dispatch_uid="interpretation_video_admin_form")
def video_admin_settings_form(sender, request=None, **kwargs):
    if PLUGIN_MODULE not in sender.get_plugins():
        return None
    return InterpretationAdminForm(
        obj=sender,
        data=request.POST if request.method == "POST" else None,
        prefix="interpretation",
    )
