from django.shortcuts import get_object_or_404
from eventyay.api.auth.permission import EventPermission
from eventyay.api.mixins import PretalxViewSetMixin
from eventyay.base.models.room import Room
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .models import RoomInterpretation
from .room_control import (
    plugin_enabled,
    serialize_room_interpretation,
    start_room_session,
    stop_room_session,
    update_room_interpretation,
)

PLUGIN_MODULE = "interpretation"


class RoomInterpretationViewSet(PretalxViewSetMixin, viewsets.ViewSet):
    """Video-admin API for per-room interpretation settings."""

    queryset = RoomInterpretation.objects.none()
    permission_classes = [EventPermission]
    write_permission = "can_change_event_settings"
    endpoint = "room_interpretation"

    def _get_room(self):
        if hasattr(self, "_room_cache"):
            return self._room_cache
        room_id = self.kwargs.get("room_pk")
        if not room_id or not self.event:
            self._room_cache = None
            return None
        self._room_cache = get_object_or_404(
            Room.objects.filter(event=self.event, deleted=False),
            pk=room_id,
        )
        return self._room_cache

    def _ensure_room(self):
        if not plugin_enabled(self.event):
            raise NotFound("Interpretation is not enabled for this event.")
        room = self._get_room()
        if room is None:
            raise NotFound("Room not found.")
        return room

    @action(detail=False, methods=["get", "patch"], url_path="config")
    def config(self, request, room_pk=None, **kwargs):
        room = self._ensure_room()
        if request.method == "GET":
            return Response(serialize_room_interpretation(room, self.event))

        try:
            interpretation = update_room_interpretation(room, self.event, request.data)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(serialize_room_interpretation(room, self.event, interpretation))

    @action(detail=False, methods=["post"], url_path="start")
    def start(self, request, room_pk=None, **kwargs):
        room = self._ensure_room()
        stream_url_override = ""
        if isinstance(request.data, dict):
            stream_url_override = (request.data.get("stream_url") or "").strip()
        result = start_room_session(
            room, self.event, stream_url_override=stream_url_override
        )
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

    @action(detail=False, methods=["post"], url_path="listener-token")
    def listener_token(self, request, room_pk=None, **kwargs):
        self._ensure_room()
        
        from .backends.voxbento_credentials import (
            get_voxbento_api_key,
            get_voxbento_base_url,
        )
        import requests

        base_url = get_voxbento_base_url(self.event)
        api_key = get_voxbento_api_key(self.event)
        
        if not base_url or not api_key:
            return Response({"detail": "VoxBento is not configured for this event."}, status=400)

        url = f"{base_url.rstrip('/')}/api/v1/tokens/listener"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        try:
            response = requests.post(url, headers=headers, timeout=5.0)
            if response.ok:
                return Response({"token": response.json().get("token")})
            else:
                return Response({"detail": f"VoxBento API Error: {response.text}"}, status=400)
        except requests.RequestException as e:
            return Response({"detail": f"Connection failed: {e}"}, status=400)
