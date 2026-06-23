import asyncio
import json

from asgiref.sync import sync_to_async
from django.contrib import messages
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View
from eventyay.control.permissions import EventPermissionRequiredMixin

from .forms import RoomInterpretationForm, SusiConnectionForm
from .models import RoomInterpretation, SusiConnection
from .services import start_stream_session
from .susi import SusiClient, SusiError
from .utils import (
    clear_module_interpretation,
    get_room_hls_url,
    set_module_interpretation,
)

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


class _RoomControlBase(InterpretationEnabledMixin, EventPermissionRequiredMixin):
    """Shared helpers for per-room interpretation control views."""

    permission = "can_change_event_settings"

    def get_connection(self):
        return SusiConnection.objects.filter(event=self.request.event).first()

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
        connection = self.get_connection()
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
        ctx["connection"] = connection
        ctx["rooms"] = rooms
        return ctx


class InterpretationRoomConfig(_RoomControlBase, FormView):
    """Edit interpretation configuration for a single room."""

    template_name = "interpretation/room_config.html"
    form_class = RoomInterpretationForm

    def get_object(self):
        room = self.get_room(self.kwargs["pk"])
        connection = self.get_connection()
        interpretation = RoomInterpretation.objects.filter(room=room).first()
        if interpretation is None and connection is not None:
            interpretation = RoomInterpretation(room=room, connection=connection)
            # Pre-fill the HLS URL from the room's stream configuration.
            interpretation.hls_url = get_room_hls_url(room)
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
        connection = self.get_connection()
        if connection is None:
            messages.error(
                self.request,
                _("Configure the SUSI server connection before setting up rooms."),
            )
            return redirect(self.get_success_url())
        interpretation = form.save(commit=False)
        interpretation.room = self.get_room(self.kwargs["pk"])
        interpretation.connection = connection
        interpretation.save()
        messages.success(self.request, _("Room interpretation settings saved."))
        return redirect(self.get_success_url())


class InterpretationRoomStart(_RoomControlBase, View):
    """Start a SUSI transcription session for a room's HLS stream."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        room = self.get_room(kwargs["pk"])
        connection = self.get_connection()
        if connection is None or not connection.is_enabled or not connection.auth_token:
            messages.error(
                request,
                _("Enable and configure the SUSI connection before starting a room."),
            )
            return redirect(self.rooms_url())

        interpretation, _created = RoomInterpretation.objects.get_or_create(
            room=room,
            defaults={"connection": connection},
        )
        if interpretation.connection_id != connection.id:
            interpretation.connection = connection

        hls_url = interpretation.hls_url or get_room_hls_url(room)
        if not hls_url:
            messages.error(
                request,
                _("No HLS stream URL is configured for this room."),
            )
            return redirect(self.rooms_url())

        client = SusiClient(connection.base_url, connection.auth_token)
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

        # Expose caption discovery info to the video frontend via module_config.
        captions_url = request.build_absolute_uri(
            reverse(
                "plugins:interpretation:room.captions",
                kwargs={
                    "organizer": self.request.event.organizer.slug,
                    "event": self.request.event.slug,
                    "pk": room.pk,
                },
            )
        )
        if set_module_interpretation(
            room,
            {
                "enabled": True,
                "languages": interpretation.target_languages,
                "url": captions_url,
            },
        ):
            room.save(update_fields=["module_config"])

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

        connection = interpretation.connection
        client = SusiClient(connection.base_url, connection.auth_token)
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

        if clear_module_interpretation(room):
            room.save(update_fields=["module_config"])

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
            connection = interpretation.connection
            client = SusiClient(connection.base_url, connection.auth_token)
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

        connection = interpretation.connection
        client = SusiClient(connection.base_url, connection.auth_token)
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


class InterpretationRoomCaptions(View):
    """Relay SUSI captions to the browser as a same-origin SSE stream.
    """

    http_method_names = ["get"]

    async def get(self, request, *args, **kwargs):
        pk = kwargs["pk"]

        @sync_to_async
        def load():
            if PLUGIN_MODULE not in request.event.get_plugins():
                return None, "disabled"
            room = get_object_or_404(request.event.rooms, pk=pk)
            interp = (
                RoomInterpretation.objects.filter(room=room)
                .select_related("connection")
                .first()
            )
            if interp is None or not interp.susi_session_id:
                return None, "nosession"
            # Snapshot the plain fields we need so no ORM access happens later.
            return {
                "base_url": interp.connection.base_url,
                "auth_token": interp.connection.auth_token,
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
