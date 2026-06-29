from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View
from eventyay.base.models import Event
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsFormView, EventSettingsViewMixin

from .forms import InterpretationSettingsForm, RoomInterpretationForm
from .models import RoomInterpretation
from .services import start_stream_session
from .settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    get_base_url,
    get_susi_client,
    is_interpretation_enabled,
    is_susi_configured,
)
from .susi import SusiClient, SusiError
from .utils import get_room_hls_url

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
                self._test_connection(form)
            else:
                messages.success(request, _("Connection settings saved."))
            return redirect(self.get_success_url())

        messages.error(
            request,
            _("Please correct the errors below before saving."),
        )
        return self.render_to_response(self.get_context_data(form=form))

    def _test_connection(self, form):
        base_url = form.cleaned_data.get(SETTING_BASE_URL) or get_base_url(
            self.request.event
        )
        token = form.cleaned_data.get(SETTING_AUTH_TOKEN, "")
        client = SusiClient(base_url, token)
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


class _RoomControlBase(InterpretationEnabledMixin, EventPermissionRequiredMixin):
    """Shared helpers for per-room interpretation control views."""

    permission = "can_change_event_settings"

    def get_room(self, pk):
        return get_object_or_404(self.request.event.rooms, pk=pk)

    def rooms_url(self):
        return reverse(
            "plugins:interpretation:rooms",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )


class InterpretationRoomList(_RoomControlBase, TemplateView):
    """List the event's rooms with their interpretation status."""

    template_name = "interpretation/rooms.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        existing = {
            ri.room_id: ri
            for ri in RoomInterpretation.objects.filter(room__event=event)
        }
        rooms = []
        for room in event.rooms.all():
            interpretation = existing.get(room.pk)
            rooms.append(
                {
                    "room": room,
                    "interpretation": interpretation,
                    "hls_url": (
                        interpretation.hls_url
                        if interpretation and interpretation.hls_url
                        else get_room_hls_url(room)
                    ),
                    "status": (
                        interpretation.status
                        if interpretation
                        else RoomInterpretation.STATUS_IDLE
                    ),
                }
            )
        ctx["event"] = event
        ctx["interpretation_enabled"] = is_susi_configured(event)
        ctx["rooms"] = rooms
        return ctx


class InterpretationRoomConfig(_RoomControlBase, FormView):
    """Edit interpretation configuration for a single room."""

    template_name = "interpretation/room_config.html"
    form_class = RoomInterpretationForm

    def get_object(self):
        room = self.get_room(self.kwargs["pk"])
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        if interpretation is None:
            interpretation = RoomInterpretation(
                room=room,
                hls_url=get_room_hls_url(room),
            )
        self.room = room
        return interpretation

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object()
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["event"] = self.request.event
        ctx["room"] = getattr(self, "room", None) or self.get_room(self.kwargs["pk"])
        ctx["detected_hls_url"] = get_room_hls_url(ctx["room"])
        return ctx

    def get_success_url(self):
        return self.rooms_url()

    def form_valid(self, form):
        if not is_susi_configured(self.request.event):
            messages.error(
                self.request,
                _("Configure the SUSI server connection before setting up rooms."),
            )
            return redirect(self.get_success_url())
        interpretation = form.save(commit=False)
        interpretation.room = self.get_room(self.kwargs["pk"])
        interpretation.save()
        messages.success(self.request, _("Room interpretation settings saved."))
        return redirect(self.get_success_url())


class InterpretationRoomStart(_RoomControlBase, View):
    """Start a SUSI transcription session for a room's HLS stream."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        event = request.event
        if not is_susi_configured(event):
            messages.error(
                request,
                _("Enable and configure the SUSI connection before starting a room."),
            )
            return redirect(self.rooms_url())

        interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)

        hls_url = interpretation.hls_url or get_room_hls_url(room)
        if not hls_url:
            messages.error(
                request,
                _("No HLS stream URL is configured for this room."),
            )
            return redirect(self.rooms_url())

        client = get_susi_client(event)
        try:
            tenant_id = start_stream_session(
                client,
                hls_url,
                source_type="url",
                transcription_provider=interpretation.transcription_provider,
                translation_provider=interpretation.translation_provider,
            )
        except SusiError as exc:
            interpretation.status = RoomInterpretation.STATUS_ERROR
            interpretation.hls_url = hls_url
            interpretation.save()
            message = str(exc)
            if "403" in message or "admin" in message.lower():
                messages.error(
                    request,
                    _(
                        "SUSI rejected the direct stream URL. Direct HLS sources "
                        "require an admin token on the SUSI server."
                    ),
                )
            else:
                messages.error(
                    request,
                    _("Could not start interpretation: %(error)s") % {"error": message},
                )
            return redirect(self.rooms_url())

        interpretation.susi_session_id = tenant_id
        interpretation.hls_url = hls_url
        interpretation.status = RoomInterpretation.STATUS_RUNNING
        interpretation.save()
        if hasattr(interpretation, "log_action"):
            interpretation.log_action(
                "interpretation.room.started",
                data={"tenant_id": tenant_id, "hls_url": hls_url},
            )
        messages.success(
            request,
            _("Interpretation started for room %(room)s.") % {"room": room.name},
        )
        return redirect(self.rooms_url())


class InterpretationRoomStop(_RoomControlBase, View):
    """Stop a room's running SUSI session."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        if interpretation is None or not interpretation.susi_session_id:
            messages.warning(
                request, _("No running interpretation session for this room.")
            )
            return redirect(self.rooms_url())

        client = get_susi_client(request.event)
        try:
            client.stop_session(interpretation.susi_session_id)
        except SusiError as exc:
            messages.error(
                request,
                _("Could not stop interpretation: %(error)s") % {"error": str(exc)},
            )
            return redirect(self.rooms_url())

        if hasattr(interpretation, "log_action"):
            interpretation.log_action(
                "interpretation.room.stopped",
                data={"tenant_id": interpretation.susi_session_id},
            )
        interpretation.status = RoomInterpretation.STATUS_STOPPED
        interpretation.susi_session_id = ""
        interpretation.save()
        messages.success(
            request,
            _("Interpretation stopped for room %(room)s.") % {"room": room.name},
        )
        return redirect(self.rooms_url())


class InterpretationRoomStatus(_RoomControlBase, View):
    """Return the warm-up status of a room's SUSI session as JSON."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        if interpretation is None:
            raise Http404("No interpretation configured for this room.")

        payload = {
            "status": interpretation.status,
            "session_id": interpretation.susi_session_id,
            "susi": None,
        }
        if interpretation.susi_session_id:
            client = get_susi_client(request.event)
            try:
                result = client.session_status(interpretation.susi_session_id)
                payload["susi"] = result.data.get("status")
            except SusiError as exc:
                payload["susi"] = "error"
                payload["error"] = str(exc)
        return JsonResponse(payload)


class InterpretationRoomTranscript(_RoomControlBase, View):
    """Read-only preview of the latest SUSI transcript for a room (testing aid).

    Proxies the request server-side so the SUSI token is never exposed to the
    browser. Intended for organizers to verify output before the attendee-facing
    caption view exists.
    """

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        if interpretation is None or not interpretation.susi_session_id:
            return JsonResponse({"transcript": "", "session": False})

        client = get_susi_client(request.event)
        try:
            result = client.latest_transcript(interpretation.susi_session_id)
        except SusiError as exc:
            return JsonResponse({"transcript": "", "session": True, "error": str(exc)})
        return JsonResponse(
            {
                "transcript": result.data.get("transcript", ""),
                "chunk_id": result.data.get("chunk_id", ""),
                "session": True,
            }
        )
