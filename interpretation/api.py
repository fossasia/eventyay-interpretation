from __future__ import annotations

import logging

from eventyay.api.auth.permission import EventPermission
from eventyay.api.mixins import PretalxViewSetMixin
from eventyay.base.models.room import Room
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from .backends.registry import get_backend
from .models import RoomInterpretation
from .room_control import (
    get_interpretation,
    plugin_enabled,
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
    update_room_interpretation,
)
from .settings import use_plugin_language_streams


class AttendeeAnonThrottle(AnonRateThrottle):
    rate = "30/minute"
    scope = "attendee_anon"


class AttendeeUserThrottle(UserRateThrottle):
    rate = "30/minute"
    scope = "attendee_user"


class AttendeeVideoPermission(BasePermission):
    """Require proof of attendee video access or organizer permissions."""

    def has_permission(self, request, view):
        event = getattr(view, "event", None)
        if not event:
            return False

        traits = getattr(request, "auth", {}).get("traits", []) if request.auth else []

        try:
            from eventyay.eventyay_common.video.permissions import video_attendee_trait

            if "attendee" in traits or video_attendee_trait(event.slug) in traits:
                return True
        except ImportError:
            if "attendee" in traits:
                return True

        # Public video access permitted by event settings (permits anonymous attendees)
        settings_obj = getattr(event, "settings", None)
        if settings_obj and settings_obj.get("venueless_show_public_link", False):
            return True

        # Check event-specific attendee or organizer access for authenticated users
        if getattr(request, "user", None) and request.user.is_authenticated:
            # Active staff / superuser session
            if hasattr(request.user, "has_active_staff_session"):
                session_key = getattr(getattr(request, "session", None), "session_key", None)
                if session_key and request.user.has_active_staff_session(session_key):
                    return True

            # Event organizer / team permission
            organizer = getattr(event, "organizer", None)
            if organizer and hasattr(request.user, "has_event_permission"):
                req_arg = request if getattr(request, "session", None) is not None else None
                if request.user.has_event_permission(organizer, event, "can_change_event_settings", request=req_arg):
                    return True

            # Valid attendee ticket / order for this event
            try:
                from eventyay.common.templatetags.event_tags import user_has_valid_ticket

                if user_has_valid_ticket({"request": request}, event):
                    return True
            except ImportError:
                pass

        return EventPermission().has_permission(request, view)


PLUGIN_MODULE = "interpretation"

logger = logging.getLogger(__name__)


class RoomInterpretationViewSet(PretalxViewSetMixin, viewsets.ViewSet):
    """Video-admin API for per-room interpretation settings."""

    queryset = RoomInterpretation.objects.none()
    permission_classes = [EventPermission]
    permission = "can_change_event_settings"
    write_permission = "can_change_event_settings"
    endpoint = "room_interpretation"

    def get_permissions(self):
        # listener_token and booth_status are attendee-facing
        if self.action in ("listener_token", "booth_status"):
            return [AttendeeVideoPermission()]
        return super().get_permissions()

    def get_throttles(self):
        if self.action in ("listener_token", "booth_status"):
            return [AttendeeAnonThrottle(), AttendeeUserThrottle()]
        return super().get_throttles()

    def _get_room(self):
        if hasattr(self, "_room_cache"):
            return self._room_cache
        room_id = self.kwargs.get("room_pk")
        if not room_id or not self.event:
            self._room_cache = None
            return None
        try:
            room_pk = int(room_id)
            if room_pk < 1 or room_pk > 9223372036854775807:
                self._room_cache = None
                return None
            self._room_cache = Room.objects.filter(event=self.event, deleted=False, pk=room_pk).first()
        except (ValueError, TypeError, OverflowError, Exception):
            self._room_cache = None
            return None
        return self._room_cache

    def _ensure_room(self):
        if not self.event or not plugin_enabled(self.event):
            raise NotFound("Interpretation is not enabled for this event.")
        room = self._get_room()
        if room is None:
            raise NotFound("Room not found.")
        return room

    @action(detail=False, methods=["get", "patch"], url_path="config")
    def config(self, request, room_pk=None, **kwargs):
        if not self.event or not plugin_enabled(self.event):
            if request.method == "GET":
                return Response(
                    {
                        "ui_sync_supported": False,
                        "plugin_enabled": False,
                        "room_enabled": False,
                        "available_interpreters": [],
                    },
                    status=200,
                )
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Interpretation plugin is disabled for this event.")

        room = self._get_room()
        if room is None:
            if request.method == "GET":
                return Response(
                    {
                        "ui_sync_supported": False,
                        "plugin_enabled": True,
                        "room_enabled": False,
                        "available_interpreters": [],
                    },
                    status=200,
                )
            raise NotFound("Room not found.")

        if request.method == "GET":
            return Response(serialize_room_interpretation(room, self.event))

        try:
            interpretation = update_room_interpretation(room, self.event, request.data)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(serialize_room_interpretation(room, self.event, interpretation))

    @action(detail=False, methods=["post"], url_path="sync")
    def sync(self, request, room_pk=None, **kwargs):
        room = self._ensure_room()
        interpretation = get_interpretation(room)
        if not interpretation or interpretation.interpreter != RoomInterpretation.INTERPRETER_VOXBENTO:
            return Response({"detail": "Room is not configured for VoxBento."}, status=400)

        backend = get_backend(interpretation.interpreter)
        from .backends.voxbento_oauth import VoxbentoTemporarilyUnavailable

        try:
            synced_count = backend.sync_booths(self.event, interpretation)
            if synced_count == 0 and interpretation.target_languages:
                return Response(
                    {"detail": "Failed to sync any booths with Voxbento. Check credentials or network."},
                    status=400,
                )
        except VoxbentoTemporarilyUnavailable:
            return Response({"detail": "VoxBento is temporarily unavailable."}, status=503)
        except Exception as e:
            return Response({"detail": str(e)}, status=500)
        return Response({"status": "synced", "synced_count": synced_count})

    @action(detail=False, methods=["post"], url_path="start")
    def start(self, request, room_pk=None, **kwargs):
        room = self._ensure_room()
        stream_url_override = ""
        if isinstance(request.data, dict):
            stream_url_override = (request.data.get("stream_url") or "").strip()
        result = start_room_session(room, self.event, stream_url_override=stream_url_override)
        if not result.ok:
            return Response({"detail": result.error}, status=400)
        payload = serialize_room_interpretation(room, self.event, result.interpretation)
        return Response(payload)

    @action(detail=False, methods=["post"], url_path="stop")
    def stop(self, request, room_pk=None, **kwargs):
        room = self._ensure_room()
        result = stop_room_session(room, self.event)
        if not result.ok:
            return Response({"detail": result.error}, status=400)
        payload = serialize_room_interpretation(room, self.event, result.interpretation)
        if result.warning:
            payload["warning"] = result.warning
        return Response(payload)

    def _make_voxbento_request(self, method, oauth_path, legacy_path=None, **kwargs):
        import requests

        from .backends.voxbento_credentials import get_voxbento_api_key, get_voxbento_base_url
        from .backends.voxbento_oauth import (
            VoxbentoReauthorizationRequired,
            VoxbentoTemporarilyUnavailable,
            get_valid_access_token,
        )

        base_url = get_voxbento_base_url(self.event)
        grant = getattr(self.event, "voxbento_oauth_grant", None)

        def get_token():
            try:
                return get_valid_access_token(grant.id) if grant else None
            except VoxbentoReauthorizationRequired:
                return None

        try:
            api_key = get_token()
        except VoxbentoTemporarilyUnavailable:
            return None, False, {"detail": "VoxBento is temporarily unavailable.", "status": 503}

        is_oauth = bool(api_key)
        if not api_key:
            api_key = get_voxbento_api_key(self.event)

        if not base_url or not api_key:
            return None, False, {"detail": "VoxBento is not configured for this event.", "status": 400}

        path = oauth_path if is_oauth else (legacy_path or oauth_path)
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {api_key}"
        if method in ("POST", "PUT", "PATCH"):
            headers.setdefault("Content-Type", "application/json")
        else:
            headers.setdefault("Accept", "application/json")

        try:
            response = requests.request(method, url, headers=headers, timeout=5.0, **kwargs)

            if response.status_code == 401 and is_oauth and grant:
                from datetime import timedelta

                from django.utils import timezone

                grant.expires_at = timezone.now() - timedelta(days=1)
                grant.save(update_fields=["expires_at"])

                try:
                    new_api_key = get_token()
                except VoxbentoTemporarilyUnavailable:
                    return None, False, {"detail": "VoxBento is temporarily unavailable.", "status": 503}
                if not new_api_key:
                    return None, False, {"detail": "VoxBento authorization expired.", "status": 401}
                headers["Authorization"] = f"Bearer {new_api_key}"
                response = requests.request(method, url, headers=headers, timeout=5.0, **kwargs)

            return response, is_oauth, None
        except requests.RequestException as e:
            return None, False, {"detail": f"Connection failed: {e}", "status": 400}

    @action(detail=False, methods=["post"], url_path="listener-token")
    def listener_token(self, request, room_pk=None, **kwargs):
        room = self._ensure_room()

        v_room_id = str(room.pk)
        if hasattr(room, "interpretation") and room.interpretation:
            interp = room.interpretation
            if interp.backend_config and interp.backend_config.get("voxbento_room_id"):
                v_room_id = str(interp.backend_config["voxbento_room_id"])
            elif interp.backend_session_id and interp.backend_session_id.isdigit():
                v_room_id = interp.backend_session_id

        oauth_path = f"api/v1/events/{self.event.slug}/rooms/{v_room_id}/listener-token"
        legacy_path = "api/v1/tokens/listener"

        payload = {"event_slug": self.event.slug}
        response, is_oauth, error = self._make_voxbento_request(
            "POST", oauth_path, legacy_path=legacy_path, json=payload
        )

        if error:
            return Response({"detail": error["detail"]}, status=error["status"])

        if response.ok:
            data = response.json()
            token = data.get("listener_token") if is_oauth else data.get("token")
            return Response({"token": token})
        else:
            logger.warning("VoxBento API Error (%s) when fetching listener token.", response.status_code)
            return Response({"detail": "Failed to retrieve listener token from VoxBento."}, status=400)

    @action(detail=False, methods=["get"], url_path="streams")
    def streams(self, request, room_pk=None, **kwargs):
        if not self.event or not plugin_enabled(self.event):
            return Response(
                {
                    "use_plugin_language_streams": False,
                    "language_streams": [],
                    "attendee_language_streams": [],
                },
                status=200,
            )
        room = self._get_room()
        if room is None:
            return Response(
                {
                    "use_plugin_language_streams": use_plugin_language_streams(self.event),
                    "language_streams": [],
                    "attendee_language_streams": [],
                },
                status=200,
            )
        payload = serialize_room_interpretation(room, self.event)
        return Response(
            {
                "use_plugin_language_streams": use_plugin_language_streams(self.event),
                "language_streams": payload["language_streams"],
                "attendee_language_streams": payload["attendee_language_streams"],
            }
        )

    @action(detail=False, methods=["get"], url_path="booth-status")
    def booth_status(self, request, room_pk=None, **kwargs):
        """Return live booth presence for this room from VoxBento.

        Attendee-facing — no organizer auth required. Proxies to the VoxBento
        /api/events/{slug}/booths endpoint so the frontend can show whether a
        human interpreter is live for a given language.
        """
        room = self._get_room()
        if room is None:
            return Response({"booths": []}, status=200)

        path = f"api/events/{self.event.slug}/booths"
        response, is_oauth, error = self._make_voxbento_request("GET", path)

        if error or not response.ok:
            return Response({"booths": []}, status=200)

        data = response.json()
        booths = data.get("booths", [])
        # Filter to only booths for this room (room_id matches)
        room_booths = [
            b
            for b in booths
            if str(b.get("room_id", "")) == str(room.pk) or str(b.get("eventyay_room_id", "")) == str(room.pk)
        ]
        return Response({"booths": room_booths})
