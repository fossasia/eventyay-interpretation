import base64
import hashlib
import os
import time
import urllib.parse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from eventyay.base.models import Event
from eventyay.base.settings import GlobalSettingsObject
from eventyay.control.permissions import EventPermissionRequiredMixin

from .models import VoxbentoOAuthGrant


def generate_pkce():
    """Generate a random code_verifier and its S256 code_challenge."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).decode("utf-8").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    return verifier, challenge


class VoxbentoOAuthConnectView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        event = self.request.event
        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        if not client_id:
            messages.error(request, _("Please configure the VoxBento Client ID in Global Settings first."))
            return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))

        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback"))

        from .backends.voxbento_credentials import get_voxbento_base_url

        voxbento_base = get_voxbento_base_url(event)
        if not voxbento_base:
            messages.error(request, _("Please configure the VoxBento Base URL in Interpreter settings first."))
            return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))

        verifier, challenge = generate_pkce()

        import secrets

        state = f"{event.slug}::{secrets.token_urlsafe(16)}"
        request.session[f"voxbento_oauth_state:{event.slug}"] = {
            "state": state,
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
        }

        auth_url = f"{voxbento_base}/oauth/authorize?{urllib.parse.urlencode(params)}"
        return redirect(auth_url)


class VoxbentoOAuthCallbackView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        state = request.GET.get("state", "")
        if "::" not in state:
            messages.error(request, _("Invalid OAuth state format."))
            return redirect(reverse("control:index"))

        event_slug, original_state = state.split("::", 1)

        try:
            event = Event.objects.get(slug=event_slug)
        except Event.DoesNotExist:
            messages.error(request, _("Event not found for OAuth callback."))
            return redirect(reverse("control:index"))

        dashboard_url = reverse(
            "plugins:interpretation:dashboard",
            kwargs={"organizer": event.organizer.slug, "event": event.slug},
        )

        error = request.GET.get("error")
        if error:
            if error == "access_denied":
                messages.error(request, _("VoxBento connection was cancelled."))
            else:
                messages.error(request, _("OAuth authorization failed: ") + error)
            return redirect(dashboard_url)

        session_data = request.session.pop(f"voxbento_oauth_state:{event.slug}", None)

        if not session_data:
            messages.error(request, _("OAuth authorization failed: Session expired or already consumed."))
            return redirect(dashboard_url)

        if session_data.get("state") != state or time.time() - session_data.get("timestamp", 0) > 600:
            messages.error(request, _("OAuth authorization failed: Invalid or expired state."))
            return redirect(dashboard_url)

        code = request.GET.get("code")
        if not code:
            messages.error(request, _("OAuth authorization failed: No code provided."))
            return redirect(dashboard_url)

        code_verifier = session_data.get("code_verifier")
        if not code_verifier:
            messages.error(request, _("OAuth authorization failed: Missing PKCE code verifier in session."))
            return redirect(dashboard_url)

        if not request.user.has_event_permission(event.organizer, event, "can_change_event_settings", request=request):
            messages.error(request, _("Permission denied for this event."))
            return redirect(dashboard_url)

        from eventyay.base.settings import GlobalSettingsObject

        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        client_secret = GlobalSettingsObject().settings.get("voxbento_client_secret", "")
        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback"))
        from .backends.voxbento_credentials import get_voxbento_base_url

        voxbento_base = get_voxbento_base_url(event)
        if not voxbento_base:
            messages.error(request, _("VoxBento Base URL is not configured."))
            return redirect(dashboard_url)

        import requests

        try:
            resp = requests.post(
                f"{voxbento_base}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()

            from datetime import timedelta

            from django.utils import timezone

            expires_in = data.get("expires_in", 3600)
            expires_at = timezone.now() + timedelta(seconds=expires_in)

            VoxbentoOAuthGrant.objects.update_or_create(
                event=event,
                defaults={
                    "access_token": data.get("access_token", ""),
                    "refresh_token": data.get("refresh_token", ""),
                    "scopes": data.get("scope", ""),
                    "expires_at": expires_at,
                    "needs_reauth": False,
                    "is_disconnected": False,
                },
            )
            from .tasks import sync_voxbento_connection

            sync_voxbento_connection.delay(event.id)

            messages.success(request, _("Successfully connected to VoxBento!"))
        except Exception as e:
            messages.error(request, _("Failed to exchange OAuth token: ") + str(e))

        return redirect(dashboard_url)
