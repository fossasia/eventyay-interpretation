from django import forms
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SECRET_REDACTED, SecretKeySettingsField, SettingsForm

from .settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
)


class InterpretationSettingsForm(SettingsForm):
    """Per-event SUSI server connection settings."""

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
            if token == SECRET_REDACTED:
                token = self.initial.get(SETTING_AUTH_TOKEN)
            if not token:
                self.add_error(
                    SETTING_AUTH_TOKEN,
                    _("An authentication token is required to enable interpretation."),
                )
        return cleaned
