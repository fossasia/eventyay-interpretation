from django.shortcuts import redirect
from django.views.generic import TemplateView

from eventyay.control.permissions import EventPermissionRequiredMixin

# Module path of this plugin as seen by eventyay's app registry.
# The plugin is installed as the top-level Django app ``interpretation``
# (see the ``pretix.plugin`` entry point), so this is the value that ends up
# in ``event.get_plugins()`` when the plugin is toggled on for an event.
PLUGIN_MODULE = "interpretation"


class InterpretationEnabledMixin:
    """Gate a view behind the per-event plugin toggle.

    The navigation entry already hides itself when the plugin is disabled
    (because ``nav_event`` is an ``EventPluginSignal``). This mixin makes
    direct URL access respect the same toggle: if the plugin is not enabled
    for the current event, the user is bounced back to the plugins page
    instead of seeing plugin UI.
    """

    def dispatch(self, request, *args, **kwargs):
        if PLUGIN_MODULE not in request.event.get_plugins():
            return redirect(
                "eventyay_common:event.plugins",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
            )
        return super().dispatch(request, *args, **kwargs)


class InterpretationDashboard(
    InterpretationEnabledMixin,
    EventPermissionRequiredMixin,
    TemplateView,
):
    """Minimal landing page proving the eventyay <-> plugin link works.

    It reads ``request.event`` (context shared by eventyay's middleware) to
    show that the plugin is wired into the running event, and reports whether
    the plugin is currently enabled for that event.
    """

    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["event"] = event
        ctx["plugin_module"] = PLUGIN_MODULE
        ctx["plugin_enabled"] = PLUGIN_MODULE in event.get_plugins()
        ctx["active_plugins"] = event.get_plugins()
        return ctx
