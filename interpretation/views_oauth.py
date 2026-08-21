import base64
import hashlib
import os

import requests
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
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
        from eventyay.base.settings import GlobalSettingsObject

        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        kwargs = {"organizer": event.organizer.slug, "event": event.slug}
        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback", kwargs=kwargs))

        from .backends.voxbento_credentials import get_voxbento_base_url

        voxbento_base = get_voxbento_base_url(event)
        if not voxbento_base:
            messages.error(request, _("Please configure the VoxBento Base URL in Interpreter settings first."))
            return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))

        verifier, challenge = generate_pkce()
        request.session["voxbento_oauth_code_verifier"] = verifier

        import secrets

        state = secrets.token_urlsafe(16)
        request.session["voxbento_oauth_state"] = state

        import urllib.parse

        scope_str = "events:read events:write rooms:write booths:read booths:write sessions:manage webhooks:manage"
        encoded_scope = urllib.parse.quote(scope_str)

        auth_url = (
            f"{voxbento_base}/oauth/authorize?response_type=code"
            f"&client_id={client_id}&redirect_uri={redirect_uri}"
            f"&scope={encoded_scope}"
            f"&code_challenge={challenge}&code_challenge_method=S256"
            f"&event={event.slug}"
            f"&state={state}"
        )
        return redirect(auth_url)


class VoxbentoOAuthCallbackView(EventPermissionRequiredMixin, View):
    permission = "can_change_event_settings"

    def get(self, request, *args, **kwargs):
        event = self.request.event
        code = request.GET.get("code")
        if not code:
            messages.error(request, _("OAuth authorization failed: No code provided."))
            kwargs = {"organizer": event.organizer.slug, "event": event.slug}
            return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))

        code_verifier = request.session.pop("voxbento_oauth_code_verifier", None)
        if not code_verifier:
            messages.error(request, _("OAuth authorization failed: Missing PKCE code verifier in session."))
            kwargs = {"organizer": event.organizer.slug, "event": event.slug}
            return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))

        from eventyay.base.settings import GlobalSettingsObject

        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        client_secret = GlobalSettingsObject().settings.get("voxbento_client_secret", "")
        kwargs = {"organizer": event.organizer.slug, "event": event.slug}
        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback", kwargs=kwargs))
        from .backends.voxbento_credentials import get_voxbento_base_url

        voxbento_base = get_voxbento_base_url(event)
        if not voxbento_base:
            messages.error(request, _("VoxBento Base URL is not configured."))
            return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))

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
                },
            )
            from .tasks import sync_voxbento_connection

            sync_voxbento_connection.delay(event.id)

            messages.success(request, _("Successfully connected to VoxBento!"))
        except Exception as e:
            messages.error(request, _("Failed to exchange OAuth token: ") + str(e))

        kwargs = {"organizer": event.organizer.slug, "event": event.slug}
        return redirect(reverse("plugins:interpretation:dashboard", kwargs=kwargs))
