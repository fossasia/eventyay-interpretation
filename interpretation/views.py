from asgiref.sync import sync_to_async
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .backends import get_backend, list_available_interpreters
from .dashboard_stats import build_overview_context
from .forms import (
    EVENT_SETTINGS_SAVE_KEY,
    INTERPRETER_ACTION_KEY,
    INTERPRETER_ID_KEY,
    PREVIEW_ACTION_KEY,
    PREVIEW_SAVE,
    PREVIEW_START,
    PREVIEW_STOP,
    ROOM_ACTION_KEY,
    ROOM_ID_KEY,
    CaptionPreviewSettingsForm,
    InterpretationSettingsForm,
    RoomConfigureForm,
    language_streams_form_prefix,
    parse_language_streams_post,
    preview_settings_payload,
    room_form_prefix,
)
from .interpreter_credentials import (
    clear_interpreter_credentials,
    get_susi_client,
    is_interpreter_configured,
    is_susi_configured,
)
from .models import RoomInterpretation
from .preview_stream import stream_susi_captions_async
from .room_control import (
    clear_room_interpretation_setup,
    get_interpretation,
    normalize_session_status,
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
    update_room_interpretation,
)
from .settings import use_plugin_language_streams

PLUGIN_MODULE = "interpretation"


def _notify_stop_result(request, room, result) -> None:
    if not result.ok:
        messages.error(request, result.error)
        return
    messages.success(
        request,
        _("Stopped interpretation for %(room)s.") % {"room": room.name},
    )
    if result.warning:
        messages.warning(request, result.warning)


def _dashboard_url(event):
    return reverse(
        "plugins:interpretation:dashboard",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _rooms_url(event):
    return reverse(
        "plugins:interpretation:rooms",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _interpreters_url(event):
    return reverse(
        "plugins:interpretation:interpreters",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _event_settings_form(event, data=None):
    return InterpretationSettingsForm(
        obj=event,
        data=data,
        prefix="interpretation",
    )


def _notify_video_room_config_changed(event) -> None:
    # ponytail: push event.updated so video SPA reloads rooms with plugin streams.
    try:
        from asgiref.sync import async_to_sync
        from eventyay.base.services.event import notify_event_change

        async_to_sync(notify_event_change)(event.id)
    except Exception:
        pass


def _process_event_settings_post(request, event, redirect_url):
    form = _event_settings_form(event, data=request.POST)
    if form.is_valid():
        form.save()
        _notify_video_room_config_changed(event)
        messages.success(request, _("Your changes have been saved."))
    else:
        messages.error(
            request,
            _("We could not save your changes. See below for details."),
        )
    return redirect(redirect_url)


class InterpretationEnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        if PLUGIN_MODULE not in request.event.get_plugins():
            return redirect(
                "eventyay_common:event.plugins",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
            )
        return super().dispatch(request, *args, **kwargs)


class InterpretationOverview(
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Plugin home: event-level status and quick navigation."""

    template_name = "interpretation/overview.html"
    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        event = request.event
        context = {
            "event": event,
            "is_event_settings": True,
            "event_settings_form": _event_settings_form(event),
            "event_settings_save_key": EVENT_SETTINGS_SAVE_KEY,
            "interpreters_url": _interpreters_url(event),
            **build_overview_context(event),
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        return _process_event_settings_post(
            request, request.event, redirect_url=_dashboard_url(request.event)
        )


class InterpretationInterpreters(
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Event-level interpreter sign-in and connection testing."""

    template_name = "interpretation/interpreters.html"
    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._context(request.event))

    def post(self, request, *args, **kwargs):
        event = request.event
        backend_id = request.POST.get(INTERPRETER_ID_KEY)
        action = request.POST.get(INTERPRETER_ACTION_KEY)
        redirect_url = _interpreters_url(event)
        backend = get_backend(backend_id or "")

        if not backend.uses_event_credentials:
            messages.error(request, _("Unknown interpreter action."))
            return redirect(redirect_url)

        if action == "connect":
            _form, ok = backend.connect(request, event, request.POST)
            if not ok:
                messages.error(
                    request,
                    _("Could not connect. Check the sign-in details below."),
                )
            return redirect(redirect_url)
        if action == "test":
            backend.test_connection(request, event)
            return redirect(redirect_url)
        if action == "disconnect":
            clear_interpreter_credentials(event, backend.id)
            messages.success(
                request,
                _("Disconnected %(name)s for this event.") % {"name": backend.label},
            )
            return redirect(redirect_url)

        messages.error(request, _("Unknown interpreter action."))
        return redirect(redirect_url)

    def _context(self, event):
        interpreters = []
        for item in list_available_interpreters(event):
            if item["id"] == RoomInterpretation.INTERPRETER_NONE:
                continue
            backend = get_backend(item["id"])
            entry = {
                "id": item["id"],
                "label": item["label"],
                "configured": item["configured"],
                "uses_event_credentials": item["uses_event_credentials"],
            }
            if backend.uses_event_credentials:
                entry["connect_form"] = backend.build_credentials_form(event=event)
                entry["account"] = backend.credentials_account_label(event)
                entry["server_host"] = backend.credentials_server_label(event)
            interpreters.append(entry)
        return {
            "event": event,
            "interpreters": interpreters,
            "rooms_url": _rooms_url(event),
            "is_event_settings": True,
        }


class InterpretationRoomSettings(
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Per-room interpreter selection and session control."""

    template_name = "interpretation/room_settings.html"
    permission = "can_change_event_settings"

    def get_success_url(self, room_id=None):
        url = _rooms_url(self.request.event)
        if room_id:
            return f"{url}?room={room_id}#room-{room_id}"
        return url

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())

    def post(self, request, *args, **kwargs):
        event = request.event
        room_id = request.POST.get(ROOM_ID_KEY)
        action = request.POST.get(ROOM_ACTION_KEY)

        if room_id:
            room = get_object_or_404(
                event.rooms.filter(deleted=False),
                pk=room_id,
            )
            prefix = room_form_prefix(room.pk)
            redirect_url = self.get_success_url(room.pk)

            if action == "save":
                return self._handle_room_save(
                    request, room, event, prefix, redirect_url
                )
            if action == "save_streams":
                return self._handle_room_streams_save(
                    request, room, event, redirect_url
                )
            if action == "disconnect":
                return self._handle_room_clear(request, room, event, redirect_url)
            if action == "stop":
                return self._handle_room_stop(request, room, event, redirect_url)

            messages.error(request, _("Unknown room action."))
            return redirect(redirect_url)

        return _process_event_settings_post(
            request, event, redirect_url=self.get_success_url()
        )

    def _apply_room_configure_form(self, request, room, event, prefix):
        form = RoomConfigureForm(request.POST, prefix=prefix, event=event)
        if not form.is_valid():
            return None, form
        try:
            interpretation = update_room_interpretation(
                room,
                event,
                {
                    "interpreter": form.cleaned_data["interpreter"],
                    "room_enabled": form.cleaned_data.get("room_enabled"),
                    "target_languages": form.cleaned_data.get("target_languages"),
                },
            )
        except ValueError as exc:
            return None, str(exc)
        return interpretation, None

    def _handle_room_save(self, request, room, event, prefix, redirect_url):
        interpretation, error = self._apply_room_configure_form(
            request, room, event, prefix
        )
        if error is not None:
            if isinstance(error, str):
                messages.error(request, error)
            else:
                messages.error(
                    request,
                    _("Could not save room settings. See below for details."),
                )
            return redirect(redirect_url)
        if (
            interpretation
            and interpretation.interpreter != RoomInterpretation.INTERPRETER_NONE
            and not is_interpreter_configured(event, interpretation.interpreter)
        ):
            backend = get_backend(interpretation.interpreter)
            messages.warning(
                request,
                _(
                    "%(name)s is not configured for this event yet. "
                    "Open Configure interpreters to sign in."
                )
                % {"name": backend.label},
            )
        elif (
            interpretation
            and interpretation.interpreter == RoomInterpretation.INTERPRETER_VOXBENTO
        ):
            backend = get_backend(interpretation.interpreter)
            backend.sync_booths(event, interpretation)

        messages.success(
            request,
            _("Saved interpretation settings for %(room)s.") % {"room": room.name},
        )
        return redirect(redirect_url)

    def _handle_room_streams_save(self, request, room, event, redirect_url):
        prefix = language_streams_form_prefix(room.pk)
        try:
            streams = parse_language_streams_post(request.POST, prefix)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect(f"{redirect_url}?room={room.pk}")
        try:
            update_room_interpretation(
                room,
                event,
                {"language_streams": streams},
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(f"{redirect_url}?room={room.pk}")
        _notify_video_room_config_changed(event)
        messages.success(
            request,
            _("Saved language streams for %(room)s.") % {"room": room.name},
        )
        return redirect(f"{redirect_url}?room={room.pk}")

    def _handle_room_clear(self, request, room, event, redirect_url):
        try:
            clear_room_interpretation_setup(room, event)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)
        messages.success(
            request,
            _("Cleared interpretation for %(room)s.") % {"room": room.name},
        )
        return redirect(redirect_url)

    def _handle_room_stop(self, request, room, event, redirect_url):
        result = stop_room_session(room, event)
        _notify_stop_result(request, room, result)
        return redirect(redirect_url)

    def get_context_data(self, **kwargs):
        event = self.request.event
        expanded_room = self.request.GET.get("room")
        existing = {
            ri.room_id: ri
            for ri in RoomInterpretation.objects.filter(room__event=event)
        }
        rooms = []
        for room in event.rooms.filter(deleted=False).order_by("name"):
            interpretation = existing.get(room.pk)
            data = serialize_room_interpretation(room, event, interpretation)
            prefix = room_form_prefix(room.pk)
            selected = data["interpreter"]
            stored_streams = list(data.get("language_streams") or [])
            stream_rows = stored_streams + [
                {"language": "", "youtube_id": "", "use_video": False}
            ]
            rooms.append(
                {
                    "room": room,
                    "data": data,
                    "streams_prefix": language_streams_form_prefix(room.pk),
                    "stream_rows": stream_rows,
                    "configure_form": RoomConfigureForm(
                        prefix=prefix,
                        event=event,
                        initial={
                            "interpreter": data["interpreter"],
                            "room_enabled": data["room_enabled"],
                            "target_languages": ", ".join(
                                data.get("target_languages", [])
                            ),
                        },
                    ),
                    "interpreter_configured": is_interpreter_configured(
                        event, selected
                    ),
                    "expanded": str(room.pk) == str(expanded_room),
                }
            )
        return {
            "event": event,
            "rooms": rooms,
            "available_interpreters": list_available_interpreters(event),
            "interpreters_url": _interpreters_url(event),
            "susi_connected": is_susi_configured(event),
            "use_plugin_language_streams": use_plugin_language_streams(event),
            "is_event_settings": True,
            **kwargs,
        }


def _preview_caption_text(data: dict | None) -> str:
    if not data:
        return ""
    return (data.get("transcript") or data.get("translation") or "").strip()


def _preview_session(room, event):
    interpretation = get_interpretation(room)
    if interpretation is None:
        return None, False
    is_running = (
        interpretation.interpreter == RoomInterpretation.INTERPRETER_SUSI
        and normalize_session_status(interpretation.status)
        == RoomInterpretation.STATUS_RUNNING
        and bool(interpretation.backend_session_id)
    )
    return interpretation, is_running


def _preview_settings_form(room, event, data=None):
    interpretation = get_interpretation(room)
    return CaptionPreviewSettingsForm(
        data=data,
        interpretation=interpretation,
    )


def _apply_preview_settings(request, room, event):
    form = _preview_settings_form(room, event, data=request.POST)
    if not form.is_valid():
        return None, form
    try:
        update_room_interpretation(room, event, preview_settings_payload(form))
    except ValueError as exc:
        return None, str(exc)
    return form, None


class InterpretationCaptionPreview(
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Temporary organizer page to verify SUSI captions reach Eventyay."""

    template_name = "interpretation/caption_preview.html"
    permission = "can_change_event_settings"

    def _room(self, event, pk):
        return get_object_or_404(event.rooms.filter(deleted=False), pk=pk)

    def _preview_url(self, room):
        return reverse(
            "plugins:interpretation:room.preview",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
                "pk": room.pk,
            },
        )

    def get(self, request, pk, *args, **kwargs):
        event = request.event
        room = self._room(event, pk)
        return render(request, self.template_name, self._context(request, event, room))

    def post(self, request, pk, *args, **kwargs):
        event = request.event
        room = self._room(event, pk)
        action = request.POST.get(PREVIEW_ACTION_KEY)
        redirect_url = self._preview_url(room)

        if action == PREVIEW_SAVE:
            _settings, error = _apply_preview_settings(request, room, event)
            if error is not None:
                if isinstance(error, str):
                    messages.error(request, error)
                else:
                    messages.error(
                        request,
                        _("Could not save preview settings. Check the fields below."),
                    )
            else:
                messages.success(request, _("Saved preview settings."))
        elif action == PREVIEW_START:
            _settings, error = _apply_preview_settings(request, room, event)
            if error is not None:
                if isinstance(error, str):
                    messages.error(request, error)
                else:
                    messages.error(
                        request,
                        _("Could not start the session. Check the settings below."),
                    )
                return redirect(redirect_url)
            result = start_room_session(room, event)
            if result.ok:
                messages.success(
                    request,
                    _("Started interpretation session for %(room)s.")
                    % {"room": room.name},
                )
            else:
                messages.error(request, result.error)
        elif action == PREVIEW_STOP:
            result = stop_room_session(room, event)
            _notify_stop_result(request, room, result)
        else:
            messages.error(request, _("Unknown preview action."))

        return redirect(redirect_url)

    def _context(self, request, event, room):
        interpretation, is_running = _preview_session(room, event)
        data = serialize_room_interpretation(room, event, interpretation)
        return {
            "event": event,
            "room": room,
            "data": data,
            "preview_form": _preview_settings_form(room, event),
            "preview_action_key": PREVIEW_ACTION_KEY,
            "is_running": is_running,
            "preview_supported": data.get("interpreter")
            == RoomInterpretation.INTERPRETER_SUSI,
            "stream_url": reverse(
                "plugins:interpretation:room.preview.stream",
                kwargs={
                    "organizer": event.organizer.slug,
                    "event": event.slug,
                    "pk": room.pk,
                },
            ),
            "rooms_url": _rooms_url(event),
            "interpreters_url": _interpreters_url(event),
            "is_event_settings": True,
        }


def _preview_sse(message: str, *, error: bool = False) -> StreamingHttpResponse:
    import json

    payload = (
        {"status": "error", "message": message} if error else {"status": "connected"}
    )
    body = f"data: {json.dumps(payload)}\n\n".encode()
    return StreamingHttpResponse(
        [body], content_type="text/event-stream; charset=utf-8"
    )


def _preview_stream_response(stream) -> StreamingHttpResponse:
    response = StreamingHttpResponse(
        stream, content_type="text/event-stream; charset=utf-8"
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


def _load_preview_stream(event, pk):
    """Sync ORM/auth lookup for preview stream (call via sync_to_async)."""
    room = get_object_or_404(event.rooms.filter(deleted=False), pk=pk)
    interpretation, is_running = _preview_session(room, event)
    if interpretation is None or not is_running:
        return None, None, str(_("Session is not running."))
    client = get_susi_client(event)
    if not client.auth_token:
        return None, None, str(_("SUSI is not connected for this event."))
    return client, interpretation.backend_session_id, None


class InterpretationCaptionPreviewStream(View):
    """Proxy SUSI caption SSE to the organizer preview page (async for Daphne)."""

    permission = "can_change_event_settings"
    http_method_names = ["get"]

    async def dispatch(self, request, *args, **kwargs):
        if PLUGIN_MODULE not in request.event.get_plugins():
            return redirect(
                "eventyay_common:event.plugins",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
            )
        if not request.user.is_authenticated:
            raise PermissionDenied()
        allowed = await sync_to_async(
            request.user.has_event_permission,
            thread_sensitive=True,
        )(request.organizer, request.event, self.permission, request=request)
        if not allowed:
            raise PermissionDenied(
                _("You do not have permission to view this content.")
            )
        self.setup(request, *args, **kwargs)
        method = request.method.lower()
        if method not in self.http_method_names:
            return await self.http_method_not_allowed(request, *args, **kwargs)
        handler = getattr(self, method, None)
        if handler is None:
            return await self.http_method_not_allowed(request, *args, **kwargs)
        return await handler(request, *args, **kwargs)

    async def get(self, request, pk, *args, **kwargs):
        client, tenant_id, error = await sync_to_async(
            _load_preview_stream,
            thread_sensitive=True,
        )(request.event, pk)
        if error:
            return _preview_sse(error, error=True)
        return _preview_stream_response(stream_susi_captions_async(client, tenant_id))
