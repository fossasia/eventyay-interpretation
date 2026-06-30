from django.dispatch import receiver
from django.urls import Resolver404, resolve, reverse
from django.utils.translation import gettext_lazy as _
from eventyay.control.signals import nav_event_common


@receiver(nav_event_common, dispatch_uid="interpretation_nav_event_common")
def navbar_entry_common(sender, request=None, **kwargs):
    if not request.user.has_event_permission(
        request.organizer,
        request.event,
        "can_change_event_settings",
        request=request,
    ):
        return []

    try:
        url = resolve(request.path_info)
        is_active = (url.namespace == "plugins:interpretation")
    except Resolver404:
        is_active = False

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
            "active": is_active,
            "icon": "language",
        }
    ]
