from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from eventyay.control.permissions import EventPermissionRequiredMixin
from eventyay.control.views.event import EventSettingsViewMixin

from .backends import get_backend, list_available_interpreters
from .backends.voxbento_oauth import VoxbentoTemporarilyUnavailable
from .dashboard_stats import build_overview_context
from .forms import (
    EVENT_SETTINGS_SAVE_KEY,
    INTERPRETER_ACTION_KEY,
    INTERPRETER_ID_KEY,
    ROOM_ACTION_KEY,
    ROOM_ID_KEY,
    InterpretationSettingsForm,
    RoomConfigureForm,
    room_form_prefix,
)
from .interpreter_credentials import (
    clear_interpreter_credentials,
    is_interpreter_configured,
)
from .models import RoomInterpretation
from .room_control import (
    clear_room_interpretation_setup,
    notify_video_room_config_changed,
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


def _process_event_settings_post(request, event, redirect_url):
    form = _event_settings_form(event, data=request.POST)
    if form.is_valid():
        form.save()
        notify_video_room_config_changed(event)
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

        grant = getattr(event, "voxbento_oauth_grant", None)
        if grant and grant.needs_reauth:
            messages.warning(
                request, _("VoxBento requires reauthorization. Please reconnect via the Configure interpreters page.")
            )

        context["voxbento_grant"] = grant
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "sync_all_rooms":
            from .backends.registry import get_backend
            from .backends.voxbento_oauth import VoxbentoTemporarilyUnavailable
            from .models import RoomInterpretation

            backend = get_backend(RoomInterpretation.INTERPRETER_VOXBENTO)
            interpretations = RoomInterpretation.objects.filter(
                room__event=request.event, interpreter=RoomInterpretation.INTERPRETER_VOXBENTO, room_enabled=True
            )
            synced = 0
            for interp in interpretations:
                try:
                    synced += backend.sync_booths(request.event, interp)
                except VoxbentoTemporarilyUnavailable:
                    messages.error(request, _("VoxBento is temporarily unavailable."))
                    return redirect(_dashboard_url(request.event))
                except Exception as e:
                    messages.error(request, _("Sync failed for room {r}: {e}").format(r=interp.room.name, e=str(e)))

            if synced > 0:
                messages.success(request, _("Successfully synced {c} interpretation booths.").format(c=synced))
            else:
                messages.warning(request, _("No interpretation booths were synced."))

            return redirect(_dashboard_url(request.event))
        return _process_event_settings_post(request, request.event, redirect_url=_dashboard_url(request.event))


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
        if action == "delete_event":
            if backend.id == "voxbento":
                from .backends.voxbento_api import delete_voxbento_event

                try:
                    delete_voxbento_event(event)
                    clear_interpreter_credentials(event, backend.id)

                    from .models import VoxbentoOAuthGrant

                    VoxbentoOAuthGrant.objects.filter(event=event).delete()

                    messages.success(request, _("Permanently deleted VoxBento event and disconnected."))
                except Exception as e:
                    messages.error(request, _("Could not delete VoxBento event: %(error)s") % {"error": str(e)})
            else:
                messages.error(request, _("Delete event not supported for this interpreter."))
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
                "is_disconnected": item.get("is_disconnected", False),
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

            if action in ("save", "sync"):
                return self._handle_room_save(request, room, event, prefix, redirect_url)
            if action == "disconnect":
                return self._handle_room_clear(request, room, event, redirect_url)
            if action == "stop":
                return self._handle_room_stop(request, room, event, redirect_url)

            messages.error(request, _("Unknown room action."))
            return redirect(redirect_url)

        return _process_event_settings_post(request, event, redirect_url=self.get_success_url())

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
        interpretation, error = self._apply_room_configure_form(request, room, event, prefix)
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
                _("%(name)s is not configured for this event yet. Open Configure interpreters to sign in.")
                % {"name": backend.label},
            )
        elif interpretation and interpretation.interpreter == RoomInterpretation.INTERPRETER_VOXBENTO:
            backend = get_backend(interpretation.interpreter)
            try:
                backend.sync_booths(event, interpretation)
            except VoxbentoTemporarilyUnavailable:
                messages.error(request, _("VoxBento is temporarily unavailable. Please try saving again later."))
                return redirect(redirect_url)

        messages.success(
            request,
            _("Saved interpretation settings for %(room)s.") % {"room": room.name},
        )
        return redirect(redirect_url)

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
        existing = {ri.room_id: ri for ri in RoomInterpretation.objects.filter(room__event=event)}
        rooms = []
        for room in event.rooms.filter(deleted=False).order_by("name"):
            interpretation = existing.get(room.pk)
            data = serialize_room_interpretation(room, event, interpretation)
            prefix = room_form_prefix(room.pk)
            selected = data["interpreter"]
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
                            "target_languages": ", ".join(data.get("target_languages", [])),
                        },
                    ),
                    "interpreter_configured": is_interpreter_configured(event, selected),
                    "expanded": str(room.pk) == str(expanded_room),
                }
            )
        return {
            "event": event,
            "rooms": rooms,
            "available_interpreters": list_available_interpreters(event),
            "interpreters_url": _interpreters_url(event),
            "is_event_settings": True,
            **kwargs,
        }
