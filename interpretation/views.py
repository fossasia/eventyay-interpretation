from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView
from eventyay.control.permissions import EventPermissionRequiredMixin

from .forms import SusiConnectionForm
from .models import SusiConnection
from .susi import SusiClient, SusiError

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
    EventPermissionRequiredMixin,
    FormView,
):
    """Configure the per-event SUSI connection and test connectivity."""

    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"
    form_class = SusiConnectionForm

    def get_object(self):
        connection = SusiConnection.objects.filter(event=self.request.event).first()
        return connection

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object()
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
        ctx["plugin_module"] = PLUGIN_MODULE
        ctx["plugin_enabled"] = PLUGIN_MODULE in event.get_plugins()
        ctx["connection"] = self.get_object()
        return ctx

    def form_valid(self, form):
        connection = form.save(commit=False)
        connection.event = self.request.event
        connection.save()

        # "Save and test" button performs an immediate connectivity check.
        if "test" in self.request.POST:
            self._test_connection(connection)
        else:
            messages.success(self.request, _("Connection settings saved."))
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        messages.error(
            self.request,
            _("Please correct the errors below before saving."),
        )
        return super().form_invalid(form)

    def _test_connection(self, connection):
        client = SusiClient(connection.base_url, connection.auth_token)
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
