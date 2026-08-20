import hashlib
import hmac
import json
import logging
import time

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

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
        logger.info("Received valid VoxBento webhook for event %s: %s", event_slug, event_type)

        return HttpResponse("OK", status=200)
