from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .forms import (
    CONNECT_POST_KEY,
    DISCONNECT_POST_KEY,
    InterpretationSettingsForm,
    TEST_POST_KEY,
)
from .settings import (
    get_base_url,
    get_susi_email,
    get_susi_name,
    is_interpretation_enabled,
    is_susi_configured,
)

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
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    FormView,
):
    """Interpretation overview and SUSI connection settings for organizers."""

    form_class = InterpretationSettingsForm
    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obj"] = self.request.event
        kwargs["prefix"] = "interpretation"
        return kwargs

    def get_success_url(self):
        return reverse(
            "plugins:interpretation:dashboard",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["event"] = event
        ctx["plugin_enabled"] = PLUGIN_MODULE in event.get_plugins()
        ctx["interpretation_enabled"] = is_interpretation_enabled(event)
        ctx["susi_configured"] = is_susi_configured(event)
        ctx["susi_server_host"] = _susi_host(get_base_url(event))
        ctx["susi_account"] = _susi_account_label(event)
        return ctx

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if DISCONNECT_POST_KEY in request.POST:
            form.run_disconnect_action(request)
            return redirect(self.get_success_url())
        if CONNECT_POST_KEY in request.POST:
            if form.is_valid():
                if form.has_changed():
                    form.save()
                form.run_connect_action(request)
            else:
                return self.form_invalid(form)
            return redirect(self.get_success_url())
        if TEST_POST_KEY in request.POST:
            if form.is_valid():
                if form.has_changed():
                    form.save()
                form.run_test_action(request)
            else:
                return self.form_invalid(form)
            return redirect(self.get_success_url())
        if form.is_valid():
            form.save()
            messages.success(request, _("Your changes have been saved."))
            return redirect(self.get_success_url())
        messages.error(
            request,
            _("We could not save your changes. See below for details."),
        )
        return self.form_invalid(form)


def _susi_account_label(event) -> str:
    name = get_susi_name(event)
    email = get_susi_email(event)
    if name and email:
        return f"{name} ({email})"
    return email or name


def _susi_host(base_url: str) -> str:
    if not base_url:
        return ""
    from urllib.parse import urlparse

    return urlparse(base_url).netloc or base_url
