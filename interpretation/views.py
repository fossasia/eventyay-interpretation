from django.shortcuts import redirect
from django.views.generic import TemplateView

from eventyay.control.permissions import EventPermissionRequiredMixin

PLUGIN_MODULE = "interpretation"


class InterpretationEnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        if PLUGIN_MODULE not in request.event.get_plugins():
            return redirect(
                "eventyay_common:event.plugins",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
            )
        return super().dispatch(request, *args, **kwargs)


class InterpretationDashboard(
    EventPermissionRequiredMixin,
    InterpretationEnabledMixin,
    TemplateView,
):
    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["event"] = event
        ctx["plugin_module"] = PLUGIN_MODULE
        ctx["plugin_enabled"] = PLUGIN_MODULE in event.get_plugins()
        return ctx
