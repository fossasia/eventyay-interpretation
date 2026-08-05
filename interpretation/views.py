from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .backends import list_available_interpreters
from .dashboard_stats import build_overview_context
from .forms import (
    CONNECT_POST_KEY,
    DISCONNECT_POST_KEY,
    EVENT_SETTINGS_SAVE_KEY,
    PREVIEW_ACTION_KEY,
    PREVIEW_SAVE,
    PREVIEW_START,
    PREVIEW_STOP,
    ROOM_ACTION_KEY,
    ROOM_ID_KEY,
    TEST_POST_KEY,
    CaptionPreviewSettingsForm,
    InterpretationSettingsForm,
    RoomConfigureForm,
    preview_settings_payload,
    room_form_prefix,
    verify_susi_connection,
)
from .models import RoomInterpretation
from .room_control import (
    clear_room_interpretation_setup,
    get_interpretation,
    normalize_session_status,
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
    update_room_interpretation,
)
from .settings import (
    get_base_url,
    get_susi_client,
    get_susi_email,
    get_susi_name,
    is_susi_connected,
)
from .susi import SusiError

PLUGIN_MODULE = "interpretation"


def _dashboard_url(event):
    return reverse(
        "plugins:interpretation:dashboard",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _event_settings_form(event, data=None):
    return InterpretationSettingsForm(
        obj=event,
        data=data,
        prefix="interpretation",
        initial={
            "interpretation_base_url": get_base_url(event),
            "susi_connect_email": get_susi_email(event) or "",
        },
    )


def _process_event_settings_post(request, event, redirect_url):
    form = _event_settings_form(event, data=request.POST)
    if DISCONNECT_POST_KEY in request.POST:
        form.run_disconnect_action(request)
        return redirect(redirect_url)
    if CONNECT_POST_KEY in request.POST:
        if form.is_valid():
            form.save_pending_connect()
            form.run_connect_action(request)
        else:
            messages.error(request, _("Could not connect to SUSI."))
        return redirect(redirect_url)
    if TEST_POST_KEY in request.POST:
        verify_susi_connection(event, request)
        return redirect(redirect_url)
    if form.is_valid():
        form.save()
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
            **build_overview_context(event),
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        return _process_event_settings_post(
            request, request.event, redirect_url=_dashboard_url(request.event)
        )


class InterpretationRoomSettings(
    InterpretationEnabledMixin,
    EventSettingsViewMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Per-room interpreter configuration and session control."""

    template_name = "interpretation/room_settings.html"
    permission = "can_change_event_settings"

    def get_success_url(self, room_id=None):
        url = reverse(
            "plugins:interpretation:rooms",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )
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
            if action == "connect":
                return self._handle_room_connect(
                    request, room, event, prefix, redirect_url
                )
            if action == "test":
                return self._handle_room_test(
                    request, room, event, prefix, redirect_url
                )
            if action == "disconnect":
                return self._handle_room_disconnect(
                    request, room, event, prefix, redirect_url
                )
            if action == "start":
                return self._handle_room_start(
                    request, room, event, prefix, redirect_url
                )
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
        messages.success(
            request,
            _("Saved interpretation settings for %(room)s.") % {"room": room.name},
        )
        return redirect(redirect_url)

    def _handle_room_connect(self, request, room, event, prefix, redirect_url):
        interpretation, error = self._apply_room_configure_form(
            request, room, event, prefix
        )
        if error is not None:
            if isinstance(error, str):
                messages.error(request, error)
            else:
                messages.error(
                    request,
                    _("Save the interpreter selection before signing in."),
                )
            return redirect(redirect_url)
        post = request.POST.copy()
        post[CONNECT_POST_KEY] = "1"
        form = InterpretationSettingsForm(obj=event, data=post, prefix=prefix)
        if not form.is_valid():
            messages.error(
                request,
                _("Could not connect. Check the sign-in details below."),
            )
            return redirect(redirect_url)
        form.save_pending_connect()
        form.run_connect_action(request)
        return redirect(redirect_url)

    def _handle_room_test(self, request, room, event, prefix, redirect_url):
        verify_susi_connection(event, request)
        return redirect(redirect_url)

    def _handle_room_disconnect(self, request, room, event, prefix, redirect_url):
        try:
            clear_room_interpretation_setup(room, event)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(redirect_url)
        messages.success(
            request,
            _(
                "Cleared interpretation for %(room)s. "
                "SUSI stays connected for other rooms."
            )
            % {"room": room.name},
        )
        return redirect(redirect_url)

    def _handle_room_start(self, request, room, event, prefix, redirect_url):
        interpretation, error = self._apply_room_configure_form(
            request, room, event, prefix
        )
        if error is not None:
            if isinstance(error, str):
                messages.error(request, error)
            else:
                messages.error(
                    request,
                    _("Could not start the session. Check the room settings below."),
                )
            return redirect(redirect_url)

        result = start_room_session(room, event)
        if result.ok:
            messages.success(
                request,
                _("Started interpretation for %(room)s.") % {"room": room.name},
            )
        else:
            messages.error(request, result.error)
        return redirect(redirect_url)

    def _handle_room_stop(self, request, room, event, redirect_url):
        result = stop_room_session(room, event)
        if result.ok:
            messages.success(
                request,
                _("Stopped interpretation for %(room)s.") % {"room": room.name},
            )
        else:
            messages.error(request, result.error)
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
            rooms.append(
                {
                    "room": room,
                    "data": data,
                    "configure_form": RoomConfigureForm(
                        prefix=prefix,
                        event=event,
                        initial={
                            "interpreter": data["interpreter"],
                            "room_enabled": data["room_enabled"],
                        },
                    ),
                    "susi_form": InterpretationSettingsForm(
                        obj=event,
                        prefix=prefix,
                        initial={
                            "interpretation_base_url": get_base_url(event),
                            "susi_connect_email": get_susi_email(event) or "",
                        },
                    ),
                    "expanded": str(room.pk) == str(expanded_room),
                }
            )
        return {
            "event": event,
            "rooms": rooms,
            "available_interpreters": list_available_interpreters(event),
            "susi_connected": is_susi_connected(event),
            "susi_server_host": _susi_host(get_base_url(event)),
            "susi_account": _susi_account_label(event),
            "is_event_settings": True,
            **kwargs,
        }


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
        return render(request, self.template_name, self._context(event, room))

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
            if result.ok:
                messages.success(
                    request,
                    _("Stopped interpretation session for %(room)s.")
                    % {"room": room.name},
                )
            else:
                messages.error(request, result.error)
        else:
            messages.error(request, _("Unknown preview action."))

        return redirect(redirect_url)

    def _context(self, event, room):
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
            "poll_url": reverse(
                "plugins:interpretation:room.preview.poll",
                kwargs={
                    "organizer": event.organizer.slug,
                    "event": event.slug,
                    "pk": room.pk,
                },
            ),
            "rooms_url": reverse(
                "plugins:interpretation:rooms",
                kwargs={
                    "organizer": event.organizer.slug,
                    "event": event.slug,
                },
            ),
            "is_event_settings": True,
        }


class InterpretationCaptionPreviewPoll(
    InterpretationEnabledMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Temporary JSON poll endpoint for the caption preview page."""

    permission = "can_change_event_settings"
    http_method_names = ["get"]

    def get(self, request, pk, *args, **kwargs):
        room = get_object_or_404(
            request.event.rooms.filter(deleted=False),
            pk=pk,
        )
        interpretation, is_running = _preview_session(room, request.event)
        if interpretation is None or not is_running:
            return JsonResponse({"running": False, "text": "", "error": ""})

        client = get_susi_client(request.event)
        try:
            result = client.latest_transcript(interpretation.backend_session_id)
            text = _preview_caption_text(result.data if result.ok else None)
            return JsonResponse({"running": True, "text": text, "error": ""})
        except SusiError as exc:
            return JsonResponse({"running": True, "text": "", "error": str(exc)})
