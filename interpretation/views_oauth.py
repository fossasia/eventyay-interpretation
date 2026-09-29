import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse
from datetime import timedelta

import redis.exceptions
import requests
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from eventyay.base.models import Event
from eventyay.base.settings import GlobalSettingsObject
from eventyay.control.permissions import EventPermissionRequiredMixin

from interpretation.operational_log import logged_request

from .backends.voxbento_credentials import get_voxbento_base_url
from .backends.voxbento_oauth import _get_cache_lock
from .models import VoxbentoOAuthGrant
from .tasks import sync_voxbento_connection

logger = logging.getLogger(__name__)


def generate_pkce():
    """Generate a random code_verifier and its S256 code_challenge."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).decode("utf-8").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    return verifier, challenge


class VoxbentoOAuthConnectView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        event = self.request.event
        dashboard_url = reverse(
            "plugins:interpretation:dashboard",
            kwargs={"organizer": kwargs.get("organizer"), "event": kwargs.get("event")},
        )
        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        if not client_id:
            msg = _("Please configure the VoxBento Client ID in Global Settings first.")
            messages.error(request, msg)
            response = render(
                request,
                "interpretation/oauth_error.html",
                {"error_message": msg, "dashboard_url": dashboard_url},
            )
            response._csp_update = {"frame-ancestors": ["'self'"]}
            return response

        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback"))
        redirect_uri = redirect_uri.split("?")[0]  # Strip automatically appended ?event= kwargs

        try:
            voxbento_base = get_voxbento_base_url(event)
        except Exception as e:
            msg = str(e)
            messages.error(request, msg)
            response = render(
                request,
                "interpretation/oauth_error.html",
                {"error_message": msg, "dashboard_url": dashboard_url},
            )
            response._csp_update = {"frame-ancestors": ["'self'"]}
            return response

        if not voxbento_base:
            msg = _("Please configure the VoxBento Base URL in Global Settings first.")
            messages.error(request, msg)
            response = render(
                request,
                "interpretation/oauth_error.html",
                {"error_message": msg, "dashboard_url": dashboard_url},
            )
            response._csp_update = {"frame-ancestors": ["'self'"]}
            return response

        verifier, challenge = generate_pkce()

        state = f"{event.slug}::{secrets.token_urlsafe(16)}"
        request.session[f"voxbento_oauth_state:{state}"] = {
            "state": state,
            "event_id": event.pk,
            "code_verifier": verifier,
            "timestamp": time.time(),
        }

        scope_str = (
            "events:read events:write rooms:write booths:read booths:write "
            "sessions:manage webhooks:manage listeners:provision"
        )

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope_str,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "event": event.slug,
            "state": state,
            "login_hint": request.user.email
            if (request.user and request.user.is_authenticated and request.user.email)
            else "",
        }

        auth_url = f"{voxbento_base}/oauth/authorize?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)


class VoxbentoOAuthCallbackView(LoginRequiredMixin, View):
    def _popup_response(self, url, success=False, error_message=""):
        context = {
            "redirect_url": url,
            "success": success,
            "error_message": error_message,
        }
        response = render(self.request, "interpretation/oauth_callback.html", context)
        response._csp_update = {
            "frame-ancestors": ["'self'"],
        }
        return response

    def get(self, request, *args, **kwargs):
        state = request.GET.get("state", "")
        if "::" not in state:
            msg = _("Invalid OAuth state format.")
            messages.error(request, msg)
            return self._popup_response(reverse("control:index"), success=False, error_message=msg)

        # Resolve identity only from a validated, single-use server-side attempt.
        # Attempts started before event_id was stored must restart authorization.
        session_data = request.session.pop(f"voxbento_oauth_state:{state}", None)
        if not session_data or not session_data.get("event_id"):
            msg = _("OAuth authorization failed: Session expired or already consumed. Please connect again.")
            messages.error(request, msg)
            return self._popup_response(reverse("control:index"), success=False, error_message=msg)

        if session_data.get("state") != state or time.time() - session_data.get("timestamp", 0) > 600:
            msg = _("OAuth authorization failed: Invalid or expired state.")
            messages.error(request, msg)
            return self._popup_response(reverse("control:index"), success=False, error_message=msg)

        try:
            event = Event.objects.get(pk=session_data["event_id"])
        except Event.DoesNotExist:
            msg = _("Event not found for OAuth callback.")
            messages.error(request, msg)
            return self._popup_response(reverse("control:index"), success=False, error_message=msg)

        if not request.user.has_event_permission(event.organizer, event, "can_change_event_settings", request=request):
            msg = _("Permission denied for this event.")
            messages.error(request, msg)
            return self._popup_response(reverse("control:index"), success=False, error_message=msg)

        try:
            dashboard_url = reverse(
                "plugins:interpretation:dashboard",
                kwargs={"organizer": event.organizer.slug, "event": event.slug},
            )
        except Exception:
            dashboard_url = f"/video/event/{event.organizer.slug}/{event.slug}/event/interpretation/"

        error = request.GET.get("error")
        error_description = request.GET.get("error_description", "")
        if error:
            if error == "access_denied":
                msg = _("VoxBento connection was cancelled.")
            elif "403" in error_description or error == "unauthorized_client":
                msg = _(
                    "OAuth authorization failed: You do not have permission. "
                    "Ensure you are a team co-owner in VoxBento or that your team settings allow this."
                )
            else:
                msg = _("OAuth authorization failed: ") + error
                if error_description:
                    msg += f" ({error_description})"
            messages.error(request, msg)
            return self._popup_response(dashboard_url, success=False, error_message=msg)

        code = request.GET.get("code")
        if not code:
            msg = _("OAuth authorization failed: No code provided.")
            messages.error(request, msg)
            return self._popup_response(dashboard_url, success=False, error_message=msg)

        code_verifier = session_data.get("code_verifier")
        if not code_verifier:
            msg = _("OAuth authorization failed: Missing PKCE code verifier in session.")
            messages.error(request, msg)
            return self._popup_response(dashboard_url, success=False, error_message=msg)

        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        client_secret = GlobalSettingsObject().settings.get("voxbento_client_secret", "")
        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback"))
        redirect_uri = redirect_uri.split("?")[0]  # Strip automatically appended ?event= kwargs

        try:
            voxbento_base = get_voxbento_base_url(event)
        except Exception as e:
            msg = str(e)
            messages.error(request, msg)
            return self._popup_response(dashboard_url, success=False, error_message=msg)

        if not voxbento_base:
            msg = _("VoxBento Base URL is not configured.")
            messages.error(request, msg)
            return self._popup_response(dashboard_url, success=False, error_message=msg)

        old_access_token = None
        old_webhook_id = None

        try:
            resp = logged_request("interpretation", "POST",
                f"{voxbento_base}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                    "event": event.slug,
                },
                timeout=(3.0, 5.0),
            )
            resp.raise_for_status()
            data = resp.json()

            expires_in = data.get("expires_in", 3600)
            expires_at = timezone.now() + timedelta(seconds=expires_in)

            defaults = {
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", ""),
                "scopes": data.get("scope", ""),
                "expires_at": expires_at,
                "needs_reauth": False,
                "is_disconnected": False,
                "webhook_subscription_id": None,
                "webhook_secret_key": None,
            }

            try:
                existing_grant = VoxbentoOAuthGrant.objects.filter(event=event).first()
                lock_key = (
                    f"voxbento:refresh:{existing_grant.id}" if existing_grant else f"voxbento:refresh:new:{event.id}"
                )
                try:
                    with _get_cache_lock(lock_key, timeout=10, blocking_timeout=12):
                        with transaction.atomic():
                            existing_grant = VoxbentoOAuthGrant.objects.select_for_update().filter(event=event).first()
                            old_access_token = existing_grant.access_token if existing_grant else None
                            old_webhook_id = existing_grant.webhook_subscription_id if existing_grant else None
                            grant, created = VoxbentoOAuthGrant.objects.update_or_create(
                                event=event,
                                defaults=defaults,
                            )
                except redis.exceptions.LockError:
                    msg = _("The integration is currently syncing. Please try again.")
                    messages.error(request, msg)
                    return self._popup_response(dashboard_url, success=False, error_message=msg)
            except IntegrityError:
                with transaction.atomic():
                    grant = VoxbentoOAuthGrant.objects.select_for_update().get(event=event)
                    old_access_token = grant.access_token
                    old_webhook_id = grant.webhook_subscription_id
                    for k, v in defaults.items():
                        setattr(grant, k, v)
                    grant.save()

            transaction.on_commit(lambda: sync_voxbento_connection.delay(event.id))
            messages.success(request, _("Successfully connected to VoxBento!"))

            if old_webhook_id and old_access_token:
                try:
                    headers = {"Authorization": f"Bearer {old_access_token}"}
                    delete_url = f"{voxbento_base.rstrip('/')}/api/v1/webhooks/{old_webhook_id}"
                    del_resp = requests.delete(delete_url, headers=headers, timeout=(3.0, 5.0))
                    if del_resp.status_code not in (204, 404):
                        logger.warning(
                            "Failed to delete old webhook during reconnect for event %s: %s",
                            event.slug,
                            del_resp.status_code,
                        )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Failed to delete old webhook during reconnect for event {event.slug}: {e}")

        except Exception as e:
            err_msg = str(e) or e.__class__.__name__
            full_msg = _("Failed to exchange OAuth token: ") + err_msg
            messages.error(request, full_msg)
            return self._popup_response(
                dashboard_url,
                success=False,
                error_message=full_msg,
            )

        return self._popup_response(dashboard_url, success=True)
