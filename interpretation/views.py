from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .backend_credentials import (
    is_susi_configured,
    susi_account_label,
    susi_server_host,
)
from .backends import list_available_interpreters
from .forms import (
    CONNECT_POST_KEY,
    InterpretationSettingsForm,
    RoomConfigureForm,
    RoomSusiCredentialsForm,
    ROOM_ACTION_KEY,
    ROOM_ID_KEY,
    room_form_prefix,
    verify_susi_connection,
)
from .models import RoomInterpretation
from .room_control import (
    clear_room_interpretation_setup,
    get_interpretation,
    serialize_room_interpretation,
    stop_room_session,
    update_room_interpretation,
)

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


def _rooms_url(event):
    return reverse(
        "plugins:interpretation:rooms",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _event_settings_form(event, data=None):
    return InterpretationSettingsForm(
        obj=event,
        data=data,
        prefix="interpretation",
    )


def _process_event_settings_post(request, event, redirect_url):
    form = _event_settings_form(event, data=request.POST)
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


class InterpretationDashboard(
    InterpretationEnabledMixin,
    EventPermissionRequiredMixin,
    View,
):
    """PR2 landing: redirect organizers to per-room settings."""

    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        return redirect(_rooms_url(request.event))


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
        form = RoomSusiCredentialsForm(
            data=post,
            prefix=prefix,
            interpretation=interpretation,
        )
        if not form.is_valid():
            messages.error(
                request,
                _("Could not connect. Check the sign-in details below."),
            )
            return redirect(redirect_url)
        form.run_connect_action(request, interpretation)
        return redirect(redirect_url)

    def _handle_room_test(self, request, room, event, prefix, redirect_url):
        interpretation = get_interpretation(room)
        if interpretation is None:
            messages.error(
                request,
                _("Configure this room before testing the connection."),
            )
            return redirect(redirect_url)
        verify_susi_connection(interpretation, request)
        return redirect(redirect_url)

    def _handle_room_disconnect(self, request, room, event, prefix, redirect_url):
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
                    "susi_form": RoomSusiCredentialsForm(
                        prefix=prefix,
                        interpretation=interpretation,
                    ),
                    "susi_connected": is_susi_configured(interpretation),
                    "susi_server_host": susi_server_host(interpretation),
                    "susi_account": susi_account_label(interpretation),
                    "expanded": str(room.pk) == str(expanded_room),
                }
            )
        return {
            "event": event,
            "rooms": rooms,
            "available_interpreters": list_available_interpreters(),
            "event_settings_form": _event_settings_form(event),
            "is_event_settings": True,
            **kwargs,
        }
