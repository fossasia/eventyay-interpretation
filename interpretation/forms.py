from django import forms
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SECRET_REDACTED, SecretKeySettingsField, SettingsForm

from .settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    get_auth_token,
    get_base_url,
)
from .susi import SusiClient, SusiError


class InterpretationSettingsForm(SettingsForm):
    """Per-event SUSI server connection settings (video admin only)."""

    interpretation_base_url = forms.URLField(
        label=_("SUSI server URL"),
        help_text=_(
            "Base URL of the SUSI Translator Flask server, "
            "e.g. https://susi.example.com"
        ),
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://susi.example.com"}),
    )
    interpretation_auth_token = SecretKeySettingsField(
        label=_("Authentication token"),
        help_text=_(
            "JWT or long-lived token used to authenticate against the SUSI "
            "server. Sent as a Bearer token; never exposed to attendees."
        ),
        required=False,
    )
    interpretation_is_enabled = forms.BooleanField(
        label=_("Enable interpretation"),
        help_text=_("Master switch for SUSI interpretation on this event."),
        required=False,
    )

    def _stored_auth_token(self) -> str:
        if not self.obj:
            return ""
        return get_auth_token(self.obj)

    def clean_interpretation_auth_token(self):
        token = (self.cleaned_data.get(SETTING_AUTH_TOKEN) or "").strip()
        if token == SECRET_REDACTED:
            token = (
                self._stored_auth_token()
                or self.initial.get(SETTING_AUTH_TOKEN)
                or ""
            ).strip()
        return token

    def clean_interpretation_base_url(self):
        url = (self.cleaned_data.get("interpretation_base_url") or "").strip()
        return url.rstrip("/")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get(SETTING_IS_ENABLED):
            if not cleaned.get(SETTING_BASE_URL):
                self.add_error(
                    SETTING_BASE_URL,
                    _("A SUSI server URL is required to enable interpretation."),
                )
            token = cleaned.get(SETTING_AUTH_TOKEN)
            if not token:
                self.add_error(
                    SETTING_AUTH_TOKEN,
                    _("An authentication token is required to enable interpretation."),
                )
        return cleaned

    def save(self):
        if self.cleaned_data.get(SETTING_AUTH_TOKEN) == SECRET_REDACTED:
            self.cleaned_data[SETTING_AUTH_TOKEN] = self._stored_auth_token()
        return super().save()


class InterpretationAdminForm(InterpretationSettingsForm):
    """Rendered on the video admin event update page when the plugin is active."""

    title = _("Interpretation (SUSI)")
    template = "interpretation/video_admin_settings.html"
    test_action_post_key = "interpretation_test_connection"

    def run_test_action(self, request):
        base_url = self.cleaned_data.get(SETTING_BASE_URL) or get_base_url(self.obj)
        token = self.cleaned_data.get(SETTING_AUTH_TOKEN, "")
        client = SusiClient(base_url, token)
        try:
            result = client.verify()
        except SusiError as exc:
            messages.error(
                request, _("Connection failed: %(error)s") % {"error": str(exc)}
            )
            return
        if result.ok:
            messages.success(
                request,
                _("Connection successful: %(message)s") % {"message": result.message},
            )
        else:
            messages.warning(
                request,
                _("Connection issue: %(message)s") % {"message": result.message},
            )
