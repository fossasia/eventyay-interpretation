import hashlib
import hmac
import json
import logging
import time

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from eventyay.base.models import Room

from .models import VoxbentoOAuthGrant

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class VoxbentoWebhookReceiverView(View):
    """
    Receives and processes webhook events from VoxBento.
    """

    def post(self, request, *args, **kwargs):
        signature_header = request.headers.get("X-VoxBento-Signature")
        if not signature_header:
            return JsonResponse({"detail": "Missing X-VoxBento-Signature header"}, status=401)

        try:
            parts = dict(p.split("=") for p in signature_header.split(","))
            timestamp = int(parts.get("t", 0))
            signature_v1 = parts.get("v1", "")
        except ValueError:
            return JsonResponse({"detail": "Invalid signature header format"}, status=401)

        current_time = int(time.time())
        if abs(current_time - timestamp) > 300:
            return JsonResponse({"detail": "Webhook timestamp too old"}, status=401)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"detail": "Invalid JSON body"}, status=400)

        event_slug = payload.get("event_slug")
        if not event_slug:
            return JsonResponse({"detail": "Missing event_slug in payload"}, status=400)

        try:
            grant = VoxbentoOAuthGrant.objects.get(event__slug=event_slug)
        except VoxbentoOAuthGrant.DoesNotExist:
            return JsonResponse({"detail": "Event not found or not connected to VoxBento"}, status=404)

        if not grant.webhook_secret_key:
            return JsonResponse({"detail": "Webhook secret key not found"}, status=401)

        # Reconstruct the signed payload: {timestamp}.{raw_request_body}
        signed_payload = f"{timestamp}.{request.body.decode('utf-8')}"

        secret = grant.webhook_secret_key.encode("utf-8")
        computed_signature = hmac.new(secret, signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_signature, signature_v1):
            return JsonResponse({"detail": "Invalid signature"}, status=401)

        event_type = payload.get("event_type")
        data = payload.get("data", {})
        logger.info("Received valid VoxBento webhook for event %s: %s", event_slug, event_type)

        handlers = {
            "booth.transcription.started": self.handle_transcription_started,
            "booth.transcription.stopped": self.handle_transcription_stopped,
            "booth.interpreter.joined": self.handle_interpreter_joined,
        }

        handler = handlers.get(event_type)
        if handler:
            handler(grant.event, data)

        return HttpResponse("OK", status=200)

    def _get_room_from_booth_id(self, event, booth_id):
        if not booth_id:
            return None
        # booth_id format is event_slug-room_id-language_code
        parts = booth_id.rsplit("-", 2)
        if len(parts) != 3:
            return None

        try:
            room_id = int(parts[1])
            return Room.objects.get(id=room_id, event=event)
        except (ValueError, Room.DoesNotExist):
            return None

    def handle_transcription_started(self, event, data):
        room = self._get_room_from_booth_id(event, data.get("booth_id"))
        if not room or not hasattr(room, "interpretation"):
            return

        interp = room.interpretation
        interp.status = interp.STATUS_RUNNING
        interp.backend_session_id = data.get("session_id", "")
        interp.save(update_fields=["status", "backend_session_id"])

        from .video_integration import notify_video_room_config_changed

        notify_video_room_config_changed(event)

    def handle_transcription_stopped(self, event, data):
        room = self._get_room_from_booth_id(event, data.get("booth_id"))
        if not room or not hasattr(room, "interpretation"):
            return

        interp = room.interpretation
        interp.status = interp.STATUS_IDLE
        interp.backend_session_id = ""
        interp.save(update_fields=["status", "backend_session_id"])

        from .video_integration import notify_video_room_config_changed

        notify_video_room_config_changed(event)

    def handle_interpreter_joined(self, event, data):
        room = self._get_room_from_booth_id(event, data.get("booth_id"))
        if not room or not hasattr(room, "interpretation"):
            return

        interp = room.interpretation
        config = interp.backend_config
        active = config.get("active_interpreters", [])

        participant = {
            "participant_id": data.get("participant_id"),
            "display_name": data.get("display_name"),
            "language": data.get("language"),
        }

        # Avoid exact duplicates
        if participant not in active:
            active.append(participant)
            config["active_interpreters"] = active
            interp.backend_config = config
            interp.save(update_fields=["backend_config"])
