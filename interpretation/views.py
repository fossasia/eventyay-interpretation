from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .backends import list_available_interpreters
from .forms import (
    CONNECT_POST_KEY,
    DISCONNECT_POST_KEY,
    ROOM_ACTION_KEY,
    ROOM_ID_KEY,
    TEST_POST_KEY,
    InterpretationSettingsForm,
    RoomConfigureForm,
    disconnect_susi_account,
    room_form_prefix,
    verify_susi_connection,
)
from .models import RoomInterpretation
from .room_control import (
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
    update_room_interpretation,
)
from .settings import get_base_url, get_susi_email, get_susi_name, is_susi_connected

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
    View,
):
    """Per-room interpretation configuration for organizers."""

    template_name = "interpretation/dashboard.html"
    permission = "can_change_event_settings"

    def get_success_url(self, room_id=None):
        url = reverse(
            "plugins:interpretation:dashboard",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )
        if room_id:
            return f"{url}?room={room_id}#room-{room_id}"
        return url

    def get(self, request, *args, **kwargs):
        from django.shortcuts import render

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
                return self._handle_room_save(request, room, event, prefix, redirect_url)
            if action == "connect":
                return self._handle_room_connect(request, room, event, prefix, redirect_url)
            if action == "test":
                return self._handle_room_test(request, room, event, prefix, redirect_url)
            if action == "disconnect":
                return self._handle_room_disconnect(request, room, event, prefix, redirect_url)
            if action == "start":
                return self._handle_room_start(request, room, event, prefix, redirect_url)
            if action == "stop":
                return self._handle_room_stop(request, room, event, redirect_url)

            messages.error(request, _("Unknown room action."))
            return redirect(redirect_url)

        form = InterpretationSettingsForm(
            obj=event,
            data=request.POST,
            prefix="interpretation",
        )
        if DISCONNECT_POST_KEY in request.POST:
            form.run_disconnect_action(request)
            return redirect(self.get_success_url())
        if CONNECT_POST_KEY in request.POST:
            if form.is_valid():
                form.save_pending_connect()
                form.run_connect_action(request)
            else:
                messages.error(request, _("Could not connect to SUSI."))
            return redirect(self.get_success_url())
        if TEST_POST_KEY in request.POST:
            verify_susi_connection(event, request)
            return redirect(self.get_success_url())
        if form.is_valid():
            form.save()
            messages.success(request, _("Your changes have been saved."))
        else:
            messages.error(
                request,
                _("We could not save your changes. See below for details."),
            )
        return redirect(self.get_success_url())

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
            _("Saved interpretation settings for %(room)s.")
            % {"room": room.name},
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
        form = InterpretationSettingsForm(
            obj=event, data=post, prefix=prefix
        )
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
        disconnect_susi_account(event, request)
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


class InterpretationRoomList(InterpretationEnabledMixin, EventPermissionRequiredMixin, View):
    """Legacy URL — redirect to the unified dashboard."""

    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        return redirect(
            reverse(
                "plugins:interpretation:dashboard",
                kwargs={
                    "organizer": request.event.organizer.slug,
                    "event": request.event.slug,
                },
            )
        )
