from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _

# ``nav_event_common`` powers the modern eventyay_common event dashboard
# sidebar (shown next to the "Settings" and "Plugins" entries). It is an
# ``EventPluginSignal``: eventyay only delivers it to receivers belonging to
# plugins that are ENABLED for the current event. That is what makes this
# navigation entry appear when the plugin is toggled on and disappear when it
# is toggled off -- no extra wiring needed.
from eventyay.control.signals import nav_event_common


@receiver(nav_event_common, dispatch_uid="interpretation_nav_event_common")
def navbar_entry_common(sender, request=None, **kwargs):
    """Add an "Interpretation" item to the eventyay_common event dashboard.

    ``sender`` is the event; the signal is only delivered while the plugin is
    enabled for that event.
    """
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
