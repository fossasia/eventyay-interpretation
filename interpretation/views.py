from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .forms import (
    CONNECT_POST_KEY,
    DISCONNECT_POST_KEY,
    InterpretationSettingsForm,
    TEST_POST_KEY,
)
from .models import RoomInterpretation
from .room_control import (
    normalize_session_status,
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
)
from .settings import (
    get_base_url,
    get_susi_client,
    get_susi_email,
    get_susi_name,
    is_interpretation_enabled,
    is_susi_configured,
    is_susi_connected,
    INTERPRETER_NONE,
    INTERPRETER_SUSI,
)
from .susi import SusiError
from .utils import video_admin_room_url

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
        ctx["interpretation_enabled"] = is_interpretation_enabled(event)
        ctx["susi_configured"] = is_susi_connected(event)
        ctx["susi_ready"] = is_susi_configured(event)
        ctx["susi_server_host"] = _susi_host(get_base_url(event))
        ctx["susi_account"] = _susi_account_label(event)
        ctx["susi_welcome_name"] = _susi_welcome_name(event)
        ctx["interpretation_providers"] = [
            {"id": INTERPRETER_NONE, "label": _("None")},
            {"id": INTERPRETER_SUSI, "label": _("SUSI Translator")},
        ]
        ctx["selected_provider"] = INTERPRETER_NONE
        if not ctx["susi_configured"]:
            form = ctx.get("form")
            if form and form.errors:
                ctx["selected_provider"] = INTERPRETER_SUSI
            elif self.request.POST.get("interpretation_provider") == INTERPRETER_SUSI:
                ctx["selected_provider"] = INTERPRETER_SUSI
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


def _susi_welcome_name(event) -> str:
    name = get_susi_name(event)
    email = get_susi_email(event)
    return name or email or ""


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


class _RoomControlBase(InterpretationEnabledMixin, EventPermissionRequiredMixin):
    """Shared helpers for per-room interpretation control views."""

    permission = "can_change_event_settings"

    def get_room(self, pk):
        return get_object_or_404(self.request.event.rooms.filter(deleted=False), pk=pk)

    def rooms_url(self):
        return reverse(
            "plugins:interpretation:rooms",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )

    def room_config_url(self, pk):
        return reverse(
            "plugins:interpretation:room.config",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
                "pk": pk,
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
        for room in event.rooms.filter(deleted=False):
            interpretation = existing.get(room.pk)
            data = serialize_room_interpretation(room, event, interpretation)
            rooms.append(
                {
                    "room": room,
                    "status": data["status"],
                    "caption_languages": data["target_languages"],
                    "video_admin_url": video_admin_room_url(
                        event.organizer.slug, event.slug, room.pk
                    ),
                }
            )
        ctx["event"] = event
        ctx["interpretation_ready"] = is_susi_configured(event)
        ctx["rooms"] = rooms
        return ctx


class InterpretationRoomConfig(_RoomControlBase, TemplateView):
    """Caption preview and session controls for a single room."""

    template_name = "interpretation/room_config.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        room = self.get_room(self.kwargs["pk"])
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        ctx["event"] = event
        ctx["room"] = room
        ctx["interpretation_ready"] = is_susi_configured(event)
        ctx["interpretation_status"] = normalize_session_status(
            interpretation.status
            if interpretation
            else RoomInterpretation.STATUS_IDLE
        )
        return ctx


class InterpretationRoomStart(_RoomControlBase, View):
    """Start a SUSI transcription session for a room's stream."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        result = start_room_session(room, request.event)
        if not result.ok:
            messages.error(request, result.error)
            return redirect(self.room_config_url(kwargs["pk"]))
        messages.success(
            request,
            _("Interpretation started for room %(room)s.") % {"room": room.name},
        )
        return redirect(self.room_config_url(kwargs["pk"]))


class InterpretationRoomStop(_RoomControlBase, View):
    """Stop a room's running SUSI session."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        result = stop_room_session(room, request.event)
        if not result.ok:
            if "No running" in result.error:
                messages.warning(request, result.error)
            else:
                messages.error(request, result.error)
            return redirect(self.room_config_url(kwargs["pk"]))
        messages.success(
            request,
            _("Interpretation stopped for room %(room)s.") % {"room": room.name},
        )
        return redirect(self.room_config_url(kwargs["pk"]))


class InterpretationRoomStatus(_RoomControlBase, View):
    """Return the warm-up status of a room's SUSI session as JSON."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        if interpretation is None:
            raise Http404("No interpretation configured for this room.")

        payload = {
            "status": normalize_session_status(interpretation.status),
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
