import base64
import hashlib
import os
import time
import urllib.parse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
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

    def post(self, request, *args, **kwargs):
        event = self.request.event
        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        if not client_id:
            messages.error(request, _("Please configure the VoxBento Client ID in Global Settings first."))
            return redirect(f"/video/event/{kwargs.get('organizer')}/{kwargs.get('event')}/event/interpretation/")

        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback"))
        redirect_uri = redirect_uri.split("?")[0]  # Strip automatically appended ?event= kwargs

        from .backends.voxbento_credentials import get_voxbento_base_url

        voxbento_base = get_voxbento_base_url(event)
        if not voxbento_base:
            messages.error(request, _("Please configure the VoxBento Base URL in Interpreter settings first."))
            return redirect(f"/video/event/{kwargs.get('organizer')}/{kwargs.get('event')}/event/interpretation/")

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

    def _popup_response(self, url):
        html = f"""
        <html><body>
        <script>
            try {{
                const bc = new BroadcastChannel('oauth_channel');
                bc.postMessage('oauth_complete');
                window.close();
            }} catch (e) {{
                console.error(e);
            }}
            setTimeout(function() {{
                if (window.opener && window.opener !== window) {{
                    window.opener.location.reload();
                    window.close();
                }} else {{
                    window.location.href = "{url}";
                }}
            }}, 500);
        </script>
        </body></html>
        """
        return HttpResponse(html)

    def get(self, request, *args, **kwargs):
        state = request.GET.get("state", "")
        if "::" not in state:
            messages.error(request, _("Invalid OAuth state format."))
            return self._popup_response(reverse("control:index"))

        event_slug, original_state = state.split("::", 1)

        try:
            event = Event.objects.get(slug=event_slug)
        except Event.DoesNotExist:
            messages.error(request, _("Event not found for OAuth callback."))
            return self._popup_response(reverse("control:index"))

        dashboard_url = f"/video/event/{event.organizer.slug}/{event.slug}/event/interpretation/"

        error = request.GET.get("error")
        error_description = request.GET.get("error_description", "")
        if error:
            if error == "access_denied":
                messages.error(request, _("VoxBento connection was cancelled."))
            elif "403" in error_description or error == "unauthorized_client":
                messages.error(
                    request,
                    _(
                        "OAuth authorization failed: You do not have permission. "
                        "Ensure you are a team co-owner in VoxBento or that your team settings allow this."
                    ),
                )
            else:
                msg = _("OAuth authorization failed: ") + error
                if error_description:
                    msg += f" ({error_description})"
                messages.error(request, msg)
            return self._popup_response(dashboard_url)

        session_data = request.session.pop(f"voxbento_oauth_state:{event.slug}", None)

        if not session_data:
            messages.error(request, _("OAuth authorization failed: Session expired or already consumed."))
            return self._popup_response(dashboard_url)

        if session_data.get("state") != state or time.time() - session_data.get("timestamp", 0) > 600:
            messages.error(request, _("OAuth authorization failed: Invalid or expired state."))
            return self._popup_response(dashboard_url)

        code = request.GET.get("code")
        if not code:
            messages.error(request, _("OAuth authorization failed: No code provided."))
            return self._popup_response(dashboard_url)

        code_verifier = session_data.get("code_verifier")
        if not code_verifier:
            messages.error(request, _("OAuth authorization failed: Missing PKCE code verifier in session."))
            return self._popup_response(dashboard_url)

        if not request.user.has_event_permission(event.organizer, event, "can_change_event_settings", request=request):
            messages.error(request, _("Permission denied for this event."))
            return self._popup_response(dashboard_url)

        from eventyay.base.settings import GlobalSettingsObject

        client_id = GlobalSettingsObject().settings.get("voxbento_client_id", "")
        client_secret = GlobalSettingsObject().settings.get("voxbento_client_secret", "")
        redirect_uri = self.request.build_absolute_uri(reverse("plugins:interpretation:oauth_callback"))
        redirect_uri = redirect_uri.split("?")[0]  # Strip automatically appended ?event= kwargs
        from .backends.voxbento_credentials import get_voxbento_base_url

        voxbento_base = get_voxbento_base_url(event)
        if not voxbento_base:
            messages.error(request, _("VoxBento Base URL is not configured."))
            return self._popup_response(dashboard_url)

        import logging

        import requests

        logger = logging.getLogger(__name__)

        old_access_token = None
        old_webhook_id = None

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
                timeout=(3.0, 5.0),
            )
            resp.raise_for_status()
            data = resp.json()

            from datetime import timedelta

            from django.db import IntegrityError, transaction
            from django.utils import timezone

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
                import redis.exceptions

                from interpretation.backends.voxbento_oauth import _get_cache_lock

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
                    messages.error(request, _("The integration is currently syncing. Please try again."))
                    return self._popup_response(dashboard_url)
            except IntegrityError:
                with transaction.atomic():
                    grant = VoxbentoOAuthGrant.objects.select_for_update().get(event=event)
                    old_access_token = grant.access_token
                    old_webhook_id = grant.webhook_subscription_id
                    for k, v in defaults.items():
                        setattr(grant, k, v)
                    grant.save()

            from .tasks import sync_voxbento_connection

            transaction.on_commit(lambda: sync_voxbento_connection.delay(event.id))

            messages.success(request, _("Successfully connected to VoxBento!"))

            if old_webhook_id and old_access_token:
                try:
                    headers = {"Authorization": f"Bearer {old_access_token}"}
                    delete_url = f"{voxbento_base.rstrip('/')}/api/v1/webhooks/{old_webhook_id}"
                    resp = requests.delete(delete_url, headers=headers, timeout=(3.0, 5.0))
                    if resp.status_code not in (204, 404):
                        logger.warning(
                            f"Failed to delete old webhook during reconnect for event {event.slug}: {resp.status_code}"
                        )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Failed to delete old webhook during reconnect for event {event.slug}: {e}")

        except Exception as e:
            messages.error(request, _("Failed to exchange OAuth token: ") + str(e))

        return self._popup_response(dashboard_url)
