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
        except ValueError:  # JSONDecodeError and UnicodeDecodeError
            return JsonResponse({"detail": "Invalid JSON body"}, status=400)

        if not isinstance(payload, dict):
            return JsonResponse({"detail": "Payload must be a JSON object"}, status=400)

        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}

        event_slug = payload.get("event_slug") or data.get("event_slug")

        if not event_slug:
            booth_id = data.get("booth_id", "")
            if isinstance(booth_id, str):
                parts = booth_id.rsplit("-", 2)
                if len(parts) == 3:
                    event_slug = parts[0]

        if not event_slug:
            logger.error("Webhook payload missing event_slug. Event identification failed.")
            return JsonResponse({"detail": "Missing event_slug in payload"}, status=400)

        grant = self._find_verified_grant(event_slug, timestamp, request.body, signature_v1)
        if grant is None:
            # Unknown event, disconnected grant, missing secret and wrong signature all look the same to the caller.
            return JsonResponse({"detail": "Invalid signature"}, status=401)

        event_type = payload.get("event_type")
        if not isinstance(event_type, str):
            # An unhashable value (list/dict) would raise TypeError in the handler lookup below.
            return JsonResponse({"detail": "event_type must be a string"}, status=400)
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

    def _find_verified_grant(self, event_slug, timestamp, body, signature):
        """
        Return the connected grant whose webhook secret signed this request, or None.

        Event slugs are only unique per organizer, so several grants can share one slug;
        the signature decides which of them the request is for.
        """
        # The signed payload is "{timestamp}.{raw_request_body}".
        signed_prefix = f"{timestamp}.".encode()
        received = signature.encode("utf-8")
        grants = VoxbentoOAuthGrant.objects.filter(event__slug=event_slug, is_disconnected=False).select_related(
            "event"
        )
        for grant in grants:
            if not grant.webhook_secret_key:
                continue
            secret = grant.webhook_secret_key.encode("utf-8")
            expected = hmac.new(secret, signed_prefix + body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected.encode("ascii"), received):
                return grant
        return None

    def _get_room_from_booth_id(self, event, booth_id):
        if not booth_id:
            return None
        # booth_id format is event_slug-room_id-language_code
        parts = booth_id.rsplit("-", 2)
        if len(parts) != 3:
            return None

        v_room_ref = parts[1]
        # 1. First check if any room in this event matches VoxBento's room ID
        room = Room.objects.filter(
            event=event,
            interpretation__backend_config__voxbento_room_id=v_room_ref,
        ).first()
        if not room:
            room = Room.objects.filter(
                event=event,
                interpretation__backend_session_id=v_room_ref,
            ).first()
        if room:
            return room

        # 2. Fallback to Eventyay's internal room database ID
        try:
            room_id = int(v_room_ref)
            return Room.objects.get(id=room_id, event=event)
        except (ValueError, Room.DoesNotExist):
            pass

        # 3. If room mapping is missing (e.g. interrupted sync), trigger async reconciliation
        try:
            from django.core.cache import cache

            from .tasks import sync_all_rooms_to_voxbento

            if cache.add(f"voxbento-resync:{event.id}", 1, 60):
                sync_all_rooms_to_voxbento.delay(event.id)
        except Exception:
            pass

        return None

    def handle_transcription_started(self, event, data):
        room = self._get_room_from_booth_id(event, data.get("booth_id"))
        if not room or not hasattr(room, "interpretation"):
            return

        from django.db import transaction

        from interpretation.models import RoomInterpretation

        with transaction.atomic():
            interp = RoomInterpretation.objects.select_for_update().get(id=room.interpretation.id)
            interp.status = interp.STATUS_RUNNING
            session_id = data.get("session_id", "")
            update_fields = ["status"]

            # Only set backend_session_id if it's currently unset.
            # Do not overwrite an existing numeric VoxBento room ID with worker UUID.
            if not interp.backend_session_id and session_id:
                interp.backend_session_id = session_id
                update_fields.append("backend_session_id")

            if session_id:
                config = dict(interp.backend_config) if interp.backend_config else {}
                config["transcription_session_id"] = session_id
                interp.backend_config = config
                update_fields.append("backend_config")

            interp.save(update_fields=update_fields)

        from .room_control import notify_video_room_config_changed

        notify_video_room_config_changed(event)

    def handle_transcription_stopped(self, event, data):
        room = self._get_room_from_booth_id(event, data.get("booth_id"))
        if not room or not hasattr(room, "interpretation"):
            return

        from django.db import transaction

        from interpretation.models import RoomInterpretation

        with transaction.atomic():
            interp = RoomInterpretation.objects.select_for_update().get(id=room.interpretation.id)
            interp.status = interp.STATUS_IDLE
            update_fields = ["status"]

            # Only clear backend_session_id if it's not a numeric VoxBento room ID
            is_numeric_room = (
                interp.interpreter == RoomInterpretation.INTERPRETER_VOXBENTO and interp.backend_session_id.isdigit()
            )
            if not is_numeric_room:
                interp.backend_session_id = ""
                update_fields.append("backend_session_id")

            config = dict(interp.backend_config) if interp.backend_config else {}
            if "transcription_session_id" in config:
                config.pop("transcription_session_id", None)
                if "backend_config" not in update_fields:
                    update_fields.append("backend_config")

            error_code = data.get("error_code")
            error_detail = data.get("error_detail")
            if error_code:
                logger.error("VoxBento session crashed for room %s: %s (%s)", room.id, error_detail, error_code)
                config["last_error"] = error_detail or error_code
                config["last_error_code"] = error_code
                if "backend_config" not in update_fields:
                    update_fields.append("backend_config")
            else:
                # Clear previous errors on normal shutdown
                if "last_error" in config:
                    config.pop("last_error", None)
                    config.pop("last_error_code", None)
                    if "backend_config" not in update_fields:
                        update_fields.append("backend_config")

            if "backend_config" in update_fields:
                interp.backend_config = config

            interp.save(update_fields=update_fields)

        from .room_control import notify_video_room_config_changed

        notify_video_room_config_changed(event)

    def handle_interpreter_joined(self, event, data):
        room = self._get_room_from_booth_id(event, data.get("booth_id"))
        if not room or not hasattr(room, "interpretation"):
            return

        from django.db import transaction

        from interpretation.models import RoomInterpretation

        with transaction.atomic():
            interp = RoomInterpretation.objects.select_for_update().get(id=room.interpretation.id)
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
