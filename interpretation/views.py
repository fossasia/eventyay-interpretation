import asyncio
import json

from asgiref.sync import sync_to_async
from django.contrib import messages
from django.http import Http404, StreamingHttpResponse
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
from .room_control import serialize_room_interpretation
from .settings import (
    get_auth_token,
    get_base_url,
    get_susi_email,
    get_susi_name,
    is_interpretation_enabled,
    is_susi_configured,
    is_susi_connected,
    INTERPRETER_NONE,
    INTERPRETER_SUSI,
)
from .susi import SusiClient, SusiError
from .utils import room_settings_url

PLUGIN_MODULE = "interpretation"

# Seconds between polls of SUSI's transcript endpoint when bridging it to SSE.
CAPTION_POLL_INTERVAL = 1.5
# Max lifetime of a single SSE connection; the browser EventSource reconnects.
CAPTION_STREAM_MAX_SECONDS = 600


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


class InterpretationRoomList(
    InterpretationEnabledMixin, EventPermissionRequiredMixin, TemplateView
):
    """List the event's rooms with their interpretation status."""

    template_name = "interpretation/rooms.html"
    permission = "can_change_event_settings"

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
                    "room_settings_url": room_settings_url(
                        event.organizer.slug, event.slug, room.pk
                    ),
                }
            )
        ctx["event"] = event
        ctx["interpretation_ready"] = is_susi_configured(event)
        ctx["rooms"] = rooms
        return ctx


class InterpretationRoomCaptions(View):
    """Relay SUSI captions to the browser as a same-origin SSE stream."""

    http_method_names = ["get"]

    async def get(self, request, *args, **kwargs):
        pk = kwargs["pk"]

        @sync_to_async
        def load():
            if PLUGIN_MODULE not in request.event.get_plugins():
                return None, "disabled"
            room = get_object_or_404(request.event.rooms.filter(deleted=False), pk=pk)
            interp = RoomInterpretation.objects.filter(room=room).first()
            if interp is None or not interp.susi_session_id:
                return None, "nosession"
            return {
                "base_url": get_base_url(request.event),
                "auth_token": get_auth_token(request.event),
                "tenant_id": interp.susi_session_id,
                "target_languages": list(interp.target_languages or []),
            }, None

        info, err = await load()
        if err == "disabled":
            raise Http404("Interpretation is not enabled for this event.")
        if err == "nosession":
            raise Http404("No running interpretation session for this room.")

        target_lang = request.GET.get("lang", "")
        if (
            target_lang
            and info["target_languages"]
            and target_lang not in info["target_languages"]
        ):
            raise Http404("Unknown caption language for this room.")

        client = SusiClient(info["base_url"], info["auth_token"])
        tenant_id = info["tenant_id"]
        poll = sync_to_async(client.latest_transcript, thread_sensitive=False)

        async def event_stream():
            yield 'data: {"status": "connected"}\n\n'
            last_serialized = None
            loops = int(CAPTION_STREAM_MAX_SECONDS / CAPTION_POLL_INTERVAL)
            for _ in range(loops):
                try:
                    result = await poll(tenant_id)
                except SusiError:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(CAPTION_POLL_INTERVAL)
                    continue

                data = result.data or {}
                transcript = data.get("transcript", "") or ""
                translation = data.get("translation", "") or ""
                if transcript or translation:
                    payload = {
                        "chunk_id": data.get("chunk_id", ""),
                        "transcript": transcript,
                        "translation": translation,
                    }
                    serialized = json.dumps(payload)
                    if serialized != last_serialized:
                        last_serialized = serialized
                        yield f"data: {serialized}\n\n"
                    else:
                        yield ": keepalive\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(CAPTION_POLL_INTERVAL)

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
