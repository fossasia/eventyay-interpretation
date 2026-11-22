import logging

from csp.decorators import csp_update
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from eventyay.control.permissions import EventPermissionRequiredMixin

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
from .operational_log import OUTCOME_FAILURE, OUTCOME_SUCCESS, log_operation, request_context
from .room_control import (
    clear_room_interpretation_setup,
    notify_video_room_config_changed,
    serialize_room_interpretation,
    stop_room_session,
    update_room_interpretation,
)

PLUGIN_MODULE = "interpretation"

logger = logging.getLogger(__name__)


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
        "plugins:interpretation:dashboard",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def _event_settings_form(event, data=None):
    return InterpretationSettingsForm(
        obj=event,
        data=data,
        prefix="interpretation",
    )


def _user_id(request):
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return getattr(user, "id", None)
    return None


def _process_event_settings_post(request, event, redirect_url):
    form = _event_settings_form(event, data=request.POST)
    if form.is_valid():
        form.save()
        notify_video_room_config_changed(event)
        log_operation(
            "interpretation.settings.update",
            OUTCOME_SUCCESS,
            backend="interpretation",
            event_id=event.id,
            user_id=_user_id(request),
        )
        messages.success(request, _("Your changes have been saved."))
    else:
        log_operation(
            "interpretation.settings.update",
            OUTCOME_FAILURE,
            backend="interpretation",
            error_code="validation_failed",
            event_id=event.id,
            user_id=_user_id(request),
        )
        messages.error(
            request,
            _("We could not save your changes. See below for details."),
        )
    return redirect(redirect_url)


class InterpretationEnabledMixin:
    def dispatch(self, request, *args, **kwargs):
        with request_context(request):
            if PLUGIN_MODULE not in request.event.get_plugins():
                log_operation(
                    "plugin.access",
                    OUTCOME_FAILURE,
                    backend="interpretation",
                    error_code="plugin_disabled",
                    event_id=getattr(request.event, "id", None),
                    user_id=_user_id(request),
                )
                return redirect(
                    "eventyay_common:event.plugins",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                )
            return super().dispatch(request, *args, **kwargs)


class XFrameOptionsSameOriginMixin:
    def dispatch(self, *args, **kwargs):
        response = super().dispatch(*args, **kwargs)
        if hasattr(response, "setdefault"):
            response.setdefault("X-Frame-Options", "SAMEORIGIN")
        else:
            response["X-Frame-Options"] = "SAMEORIGIN"

        # Modern browsers prioritize CSP frame-ancestors over X-Frame-Options.
        # Ensure frame-ancestors 'self' is applied across django-csp and Eventyay middleware.
        if not hasattr(response, "_csp_update") or response._csp_update is None:
            response._csp_update = {}
        response._csp_update.pop("FRAME_ANCESTORS", None)
        response._csp_update.pop("SCRIPT_SRC", None)
        response._csp_update["frame-ancestors"] = ["'self'"]

        if "Content-Security-Policy" in response:
            csp = response["Content-Security-Policy"]
            if "frame-ancestors" not in csp:
                response["Content-Security-Policy"] = f"{csp.rstrip('; ')}; frame-ancestors 'self'"

        return response


@method_decorator(
    csp_update(
        {
            "script-src": ["'self'", "'unsafe-eval'"],
            "frame-ancestors": ["'self'"],
        }
    ),
    name="dispatch",
)
class InterpretationOverview(
    XFrameOptionsSameOriginMixin,
    InterpretationEnabledMixin,
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

        interpreter_view = InterpretationInterpreters()
        interpreter_view.request = request
        interpreter_view.args = args
        interpreter_view.kwargs = kwargs
        context.update(interpreter_view._context(event))

        grant = getattr(event, "voxbento_oauth_grant", None)
        if grant and grant.needs_reauth:
            messages.warning(
                request, _("VoxBento requires reauthorization. Please reconnect via the Configure interpreters page.")
            )

        room_view = InterpretationRoomSettings()
        room_view.request = request
        room_view.args = args
        room_view.kwargs = kwargs
        room_context = room_view.get_context_data()
        context.update(room_context)
        context["voxbento_grant"] = grant

        from eventyay.base.settings import GlobalSettingsObject

        from .backends.voxbento_credentials import get_voxbento_base_url

        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        voxbento_base = ""
        try:
            voxbento_base = get_voxbento_base_url(event)
        except Exception:
            pass
        context["voxbento_globally_configured"] = bool(client_id and voxbento_base)

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):

        if INTERPRETER_ACTION_KEY in request.POST:
            interpreter_view = InterpretationInterpreters()
            interpreter_view.request = request
            interpreter_view.args = args
            interpreter_view.kwargs = kwargs
            return interpreter_view.post(request, *args, **kwargs)
        if ROOM_ID_KEY in request.POST:
            room_view = InterpretationRoomSettings()
            room_view.request = request
            room_view.args = args
            room_view.kwargs = kwargs
            return room_view.post(request, *args, **kwargs)

        if request.POST.get("action") == "sync_all_rooms":
            from .backends.registry import get_backend
            from .backends.voxbento_api import create_voxbento_event, subscribe_to_voxbento_webhooks
            from .backends.voxbento_oauth import VoxbentoTemporarilyUnavailable
            from .models import RoomInterpretation

            grant = getattr(request.event, "voxbento_oauth_grant", None)
            if grant and not grant.is_disconnected:
                if not grant.event_provisioned or grant.event_provisioning_failed:
                    try:
                        create_voxbento_event(request.event)
                        grant.refresh_from_db()
                        if not grant.event_provisioned or grant.event_provisioning_failed:
                            raise RuntimeError(_("VoxBento event provisioning did not complete"))
                    except Exception as e:
                        logger.warning("Retry sync create_voxbento_event failed: %s", e)
                        grant.event_provisioning_failed = True
                        grant.save(update_fields=["event_provisioning_failed"])
                        messages.warning(request, _("VoxBento event provisioning failed: {e}").format(e=str(e)))

                if not grant.webhook_subscription_id or grant.webhook_subscription_failed:
                    try:
                        subscribe_to_voxbento_webhooks(request.event)
                        grant.refresh_from_db()
                        if (
                            not grant.webhook_subscription_id and not grant.webhook_scope_denied
                        ) or grant.webhook_subscription_failed:
                            raise RuntimeError(_("VoxBento webhook setup did not complete"))
                    except Exception as e:
                        logger.warning("Retry sync subscribe_to_voxbento_webhooks failed: %s", e)
                        grant.webhook_subscription_failed = True
                        grant.save(update_fields=["webhook_subscription_failed"])
                        messages.warning(request, _("VoxBento webhook setup failed: {e}").format(e=str(e)))

            backend = get_backend(RoomInterpretation.INTERPRETER_VOXBENTO)
            interpretations = RoomInterpretation.objects.filter(
                room__event=request.event, interpreter=RoomInterpretation.INTERPRETER_VOXBENTO, room_enabled=True
            )
            synced = 0
            for interp in interpretations:
                try:
                    synced += backend.sync_booths(request.event, interp)
                except VoxbentoTemporarilyUnavailable:
                    log_operation(
                        "interpretation.sync_all",
                        OUTCOME_FAILURE,
                        backend="voxbento",
                        error_code="backend_unavailable",
                        event_id=request.event.id,
                        user_id=_user_id(request),
                    )
                    messages.error(request, _("VoxBento is temporarily unavailable."))
                    return redirect(_dashboard_url(request.event))
                except Exception as e:
                    log_operation(
                        "interpretation.sync_all",
                        OUTCOME_FAILURE,
                        backend="voxbento",
                        error_code=type(e).__name__,
                        event_id=request.event.id,
                        object_id=interp.room_id,
                        user_id=_user_id(request),
                    )
                    messages.error(request, _("Sync failed for room {r}: {e}").format(r=interp.room.name, e=str(e)))

            if synced > 0:
                log_operation(
                    "interpretation.sync_all",
                    OUTCOME_SUCCESS,
                    backend="voxbento",
                    event_id=request.event.id,
                    user_id=_user_id(request),
                )
                messages.success(request, _("Successfully synced {c} interpretation booths.").format(c=synced))
            else:
                log_operation(
                    "interpretation.sync_all",
                    OUTCOME_FAILURE,
                    backend="voxbento",
                    error_code="no_booths_synced",
                    event_id=request.event.id,
                    user_id=_user_id(request),
                )
                messages.warning(request, _("No interpretation booths were synced."))

            return redirect(_dashboard_url(request.event))
        return _process_event_settings_post(request, request.event, redirect_url=_dashboard_url(request.event))


class InterpretationInterpreters(
    XFrameOptionsSameOriginMixin,
    InterpretationEnabledMixin,
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
        redirect_url = _dashboard_url(event)
        backend = get_backend(backend_id or "")

        if not backend.uses_event_credentials:
            log_operation(
                "interpreter.config",
                OUTCOME_FAILURE,
                backend=backend_id or "none",
                error_code="unknown_action",
                event_id=event.id,
                user_id=_user_id(request),
            )
            messages.error(request, _("Unknown interpreter action."))
            return redirect(redirect_url)

        if action == "connect":
            _form, ok = backend.connect(request, event, request.POST)
            log_operation(
                "interpreter.connect",
                OUTCOME_SUCCESS if ok else OUTCOME_FAILURE,
                backend=backend.id,
                error_code=None if ok else "connect_failed",
                event_id=event.id,
                user_id=_user_id(request),
            )
            if not ok:
                messages.error(
                    request,
                    _("Could not connect. Check the sign-in details below."),
                )
            return redirect(redirect_url)
        if action == "test":
            backend.test_connection(request, event)
            log_operation(
                "interpreter.test",
                OUTCOME_SUCCESS,
                backend=backend.id,
                event_id=event.id,
                user_id=_user_id(request),
            )
            return redirect(redirect_url)
        if action == "disconnect":
            clear_interpreter_credentials(event, backend.id)
            log_operation(
                "interpreter.disconnect",
                OUTCOME_SUCCESS,
                backend=backend.id,
                event_id=event.id,
                user_id=_user_id(request),
            )
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

                    log_operation(
                        "interpreter.delete_event",
                        OUTCOME_SUCCESS,
                        backend=backend.id,
                        event_id=event.id,
                        user_id=_user_id(request),
                    )
                    messages.success(request, _("Permanently deleted VoxBento event and disconnected."))
                except Exception as e:
                    log_operation(
                        "interpreter.delete_event",
                        OUTCOME_FAILURE,
                        backend=backend.id,
                        error_code=type(e).__name__,
                        event_id=event.id,
                        user_id=_user_id(request),
                    )
                    messages.error(request, _("Could not delete VoxBento event: %(error)s") % {"error": str(e)})
            else:
                log_operation(
                    "interpreter.delete_event",
                    OUTCOME_FAILURE,
                    backend=backend.id,
                    error_code="unsupported_backend",
                    event_id=event.id,
                    user_id=_user_id(request),
                )
                messages.error(request, _("Delete event not supported for this interpreter."))
            return redirect(redirect_url)

        log_operation(
            "interpreter.config",
            OUTCOME_FAILURE,
            backend=backend.id,
            error_code="unknown_action",
            event_id=event.id,
            user_id=_user_id(request),
        )
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
        grant = getattr(event, "voxbento_oauth_grant", None)
        return {
            "event": event,
            "interpreters": interpreters,
            "rooms_url": _rooms_url(event),
            "is_event_settings": True,
            "voxbento_grant": grant,
        }


class InterpretationRoomSettings(
    XFrameOptionsSameOriginMixin,
    InterpretationEnabledMixin,
    EventPermissionRequiredMixin,
    View,
):
    """Per-room interpreter selection and session control."""

    template_name = "interpretation/overview.html"
    permission = "can_change_event_settings"

    def get_success_url(self, room_id=None):
        url = _dashboard_url(self.request.event)
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

            log_operation(
                "room.config.update",
                OUTCOME_FAILURE,
                backend="interpretation",
                error_code="unknown_action",
                event_id=event.id,
                object_id=room.pk,
                user_id=_user_id(request),
            )
            messages.error(request, _("Unknown room action."))
            return redirect(redirect_url)

        return _process_event_settings_post(request, event, redirect_url=self.get_success_url())

    def _apply_room_configure_form(self, request, room, event, prefix):
        form = RoomConfigureForm(request.POST, prefix=prefix, event=event)
        if not form.is_valid():
            return None, form
        try:
            from django.db import transaction

            sync_needed = False

            with transaction.atomic():
                # Handle API Keys first inside transaction
                api_keys = [
                    "openai_api_key",
                    "deepgram_api_key",
                    "nvidia_api_key",
                    "elevenlabs_api_key",
                    "translation_openai_api_key",
                    "openrouter_api_key",
                    "gemini_api_key",
                    "anthropic_api_key",
                    "groq_api_key",
                ]

                grant = None
                updated_keys = False
                if form.cleaned_data.get("interpreter") == "voxbento":
                    from .models import VoxbentoOAuthGrant

                    grant = VoxbentoOAuthGrant.objects.filter(event=event).first()
                    if grant:
                        for key in api_keys:
                            val = form.cleaned_data.get(key)
                            if val:
                                setattr(grant, key, val)
                                updated_keys = True

                        from .api_key_validator import validate_provider_key

                        tp = form.cleaned_data.get("transcription_provider")
                        if form.cleaned_data.get("enable_transcription") and tp and tp != "none" and tp != "local":
                            key_val = getattr(grant, f"{tp}_api_key", None)
                            if key_val and not validate_provider_key(tp, key_val):
                                return None, f"The API key for {tp.capitalize()} is invalid, expired, or revoked."

                        vp = form.cleaned_data.get("translation_provider")
                        if form.cleaned_data.get("enable_translation") and vp and vp != "none" and vp != "local":
                            key_name = "translation_openai_api_key" if vp == "openai" else f"{vp}_api_key"
                            key_val = getattr(grant, key_name, None)
                            if key_val and not validate_provider_key(vp, key_val):
                                return (
                                    None,
                                    f"The API key for {vp.capitalize()} (Translation) is invalid, expired, or revoked.",
                                )

                # Validation passed, safe to update room interpretation
                interpretation = update_room_interpretation(
                    room,
                    event,
                    {
                        "interpreter": form.cleaned_data["interpreter"],
                        "room_enabled": form.cleaned_data.get("room_enabled"),
                        "enable_transcription": form.cleaned_data.get("enable_transcription"),
                        "transcription_provider": form.cleaned_data.get("transcription_provider"),
                        "transcription_model": form.cleaned_data.get("transcription_model"),
                        "source_language": form.cleaned_data.get("source_language"),
                        "enable_translation": form.cleaned_data.get("enable_translation"),
                        "translation_provider": form.cleaned_data.get("translation_provider"),
                        "translation_model": form.cleaned_data.get("translation_model"),
                    },
                )

                if grant and updated_keys:
                    grant.save(update_fields=api_keys)
                    sync_needed = True

            # Sync after atomic block to avoid locking during network request
            if sync_needed:
                from .backends.voxbento_api import sync_voxbento_api_keys

                sync_voxbento_api_keys(event)

        except ValueError as exc:
            return None, str(exc)
        except Exception as exc:
            from .backends.voxbento_oauth import VoxbentoReauthorizationRequired

            if isinstance(exc, VoxbentoReauthorizationRequired):
                return None, str(
                    _("VoxBento requires reauthorization. Please reconnect via the Configure interpreters page.")
                )
            raise
        return interpretation, None

    def _handle_room_save(self, request, room, event, prefix, redirect_url):
        interpretation, error = self._apply_room_configure_form(request, room, event, prefix)
        if error is not None:
            log_operation(
                "room.config.update",
                OUTCOME_FAILURE,
                backend="interpretation",
                error_code="validation_failed",
                event_id=event.id,
                object_id=room.pk,
                user_id=_user_id(request),
            )
            if isinstance(error, str):
                messages.error(request, error)
                is_api_error = "invalid, expired, or revoked" in error

                invalid_keys = {}
                if is_api_error:
                    # Quick parse the error to find the provider so we can highlight the right box
                    if "(Translation)" in error:
                        for p in ["openai", "openrouter", "gemini", "anthropic", "groq"]:
                            if p in error:
                                invalid_keys["translation_openai" if p == "openai" else p] = True
                    else:
                        for p in ["openai", "deepgram", "nvidia", "elevenlabs"]:
                            if p in error:
                                invalid_keys[p] = True

                form = RoomConfigureForm(request.POST, prefix=prefix, event=event, invalid_api_keys=invalid_keys)
            else:
                for field, field_errors in error.errors.items():
                    for err in field_errors:
                        messages.error(request, err)
                form = error

            context = self.get_context_data()
            for room_data in context["rooms"]:
                if room_data["room"].pk == room.pk:
                    room_data["configure_form"] = form
                    room_data["expanded"] = True
            from django.shortcuts import render

            return render(request, self.template_name, context)
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
                log_operation(
                    "room.config.update",
                    OUTCOME_FAILURE,
                    backend=interpretation.interpreter,
                    error_code="backend_unavailable",
                    event_id=event.id,
                    object_id=room.pk,
                    user_id=_user_id(request),
                )
                messages.error(request, _("VoxBento is temporarily unavailable. Please try saving again later."))
                return redirect(redirect_url)

        log_operation(
            "room.config.update",
            OUTCOME_SUCCESS,
            backend=interpretation.interpreter if interpretation else "none",
            event_id=event.id,
            object_id=room.pk,
            user_id=_user_id(request),
        )
        messages.success(
            request,
            _("Saved interpretation settings for %(room)s.") % {"room": room.name},
        )
        return redirect(redirect_url)

    def _handle_room_clear(self, request, room, event, redirect_url):
        try:
            clear_room_interpretation_setup(room, event)
        except ValueError as exc:
            log_operation(
                "room.config.clear",
                OUTCOME_FAILURE,
                backend="interpretation",
                error_code="clear_failed",
                event_id=event.id,
                object_id=room.pk,
                user_id=_user_id(request),
            )
            messages.error(request, str(exc))
            return redirect(redirect_url)
        log_operation(
            "room.config.clear",
            OUTCOME_SUCCESS,
            backend="interpretation",
            event_id=event.id,
            object_id=room.pk,
            user_id=_user_id(request),
        )
        messages.success(
            request,
            _("Cleared interpretation for %(room)s.") % {"room": room.name},
        )
        return redirect(redirect_url)

    def _handle_room_stop(self, request, room, event, redirect_url):
        result = stop_room_session(room, event)
        log_operation(
            "room.session.stop",
            OUTCOME_SUCCESS if result.ok else OUTCOME_FAILURE,
            backend=result.interpretation.interpreter if result.interpretation else "interpretation",
            error_code=None if result.ok else "stop_failed",
            event_id=event.id,
            object_id=room.pk,
            user_id=_user_id(request),
        )
        _notify_stop_result(request, room, result)
        return redirect(redirect_url)

    def get_context_data(self, **kwargs):
        event = self.request.event
        expanded_room = self.request.GET.get("room")
        existing = {ri.room_id: ri for ri in RoomInterpretation.objects.filter(room__event=event)}

        grant = getattr(event, "voxbento_oauth_grant", None)
        try:
            from .backends.voxbento_credentials import get_voxbento_base_url

            voxbento_base_url = get_voxbento_base_url(event)
        except Exception:
            voxbento_base_url = ""
        rooms = []
        for room in event.rooms.filter(deleted=False).order_by("name"):
            interpretation = existing.get(room.pk)
            data = serialize_room_interpretation(room, event, interpretation)
            prefix = room_form_prefix(room.pk)
            selected = data["interpreter"]

            api_key_error = None
            invalid_api_keys = {}

            vox_room_url = None
            if selected == "voxbento" and voxbento_base_url:
                if interpretation and interpretation.backend_session_id:
                    room_ref = interpretation.backend_session_id
                    vox_room_url = (
                        f"{voxbento_base_url.rstrip('/')}/workspace/events/by-slug/{event.slug}/rooms/{room_ref}/?by=id"
                    )
                else:
                    room_ref = room.pk
                    base = voxbento_base_url.rstrip("/")
                    vox_room_url = f"{base}/workspace/events/by-slug/{event.slug}/rooms/by-eventyay/{room_ref}/"

            rooms.append(
                {
                    "room": room,
                    "data": data,
                    "voxbento_room_url": vox_room_url,
                    "api_key_error": api_key_error,
                    "configure_form": RoomConfigureForm(
                        prefix=prefix,
                        event=event,
                        invalid_api_keys=invalid_api_keys,
                        initial={
                            "interpreter": data["interpreter"],
                            "room_enabled": data["room_enabled"],
                            "enable_transcription": data["enable_transcription"],
                            "transcription_provider": data["transcription_provider"],
                            "transcription_model": data["transcription_model"],
                            "source_language": data["source_language"],
                            "enable_translation": data["enable_translation"],
                            "translation_provider": data["translation_provider"],
                            "translation_model": data["translation_model"],
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
            "voxbento_grant": grant,
            **kwargs,
        }
