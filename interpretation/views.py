from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .settings import (
    is_interpretation_enabled,
    is_susi_configured,
    get_base_url,
)

PLUGIN_MODULE = "interpretation"


class InterpretationEnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        if PLUGIN_MODULE not in request.event.get_plugins():
            from django.shortcuts import redirect

            return redirect(
                "eventyay_common:event.plugins",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
            )
        return super().dispatch(request, *args, **kwargs)


class InterpretationDashboard(
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    TemplateView,
):
    """Read-only overview of interpretation status for event organizers."""

    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["event"] = event
        ctx["plugin_enabled"] = PLUGIN_MODULE in event.get_plugins()
        ctx["interpretation_enabled"] = is_interpretation_enabled(event)
        ctx["susi_configured"] = is_susi_configured(event)
        ctx["susi_server_host"] = _susi_host(get_base_url(event))
        return ctx


def _susi_host(base_url: str) -> str:
    if not base_url:
        return ""
    from urllib.parse import urlparse

    return urlparse(base_url).netloc or base_url
