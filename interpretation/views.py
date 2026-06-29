from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from eventyay.base.models import Event
from eventyay.control.views.event import EventSettingsFormView, EventSettingsViewMixin

from .forms import InterpretationSettingsForm
from .settings import get_susi_client, is_interpretation_enabled
from .susi import SusiError

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
    EventSettingsFormView,
):
    """Configure the per-event SUSI connection and test connectivity."""

    model = Event
    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"
    form_class = InterpretationSettingsForm

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
        ctx["plugin_module"] = PLUGIN_MODULE
        ctx["plugin_enabled"] = PLUGIN_MODULE in event.get_plugins()
        ctx["interpretation_enabled"] = is_interpretation_enabled(event)
        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if form.is_valid():
            form.save()
            self._save_decoupled(form)
            if form.has_changed():
                request.event.log_action(
                    "eventyay.event.settings",
                    user=request.user,
                    data={
                        k: form.cleaned_data.get(k)
                        for k in form.changed_data
                        if k != "interpretation_auth_token"
                    },
                )
            if "test" in request.POST:
                self._test_connection()
            else:
                messages.success(request, _("Connection settings saved."))
            return redirect(self.get_success_url())

        messages.error(
            request,
            _("Please correct the errors below before saving."),
        )
        return self.render_to_response(self.get_context_data(form=form))

    def _test_connection(self):
        client = get_susi_client(self.request.event)
        try:
            result = client.verify()
        except SusiError as exc:
            messages.error(
                self.request, _("Connection failed: %(error)s") % {"error": str(exc)}
            )
            return
        if result.ok:
            messages.success(
                self.request,
                _("Connection successful: %(message)s") % {"message": result.message},
            )
        else:
            messages.warning(
                self.request,
                _("Connection issue: %(message)s") % {"message": result.message},
            )
