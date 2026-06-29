from django import forms
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SECRET_REDACTED, SecretKeySettingsField, SettingsForm

from .models import RoomInterpretation
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

    def _stored_auth_token(self) -> str:
        if not self.obj:
            return ""
        return self.obj.settings.get(SETTING_AUTH_TOKEN, default="", as_type=str)

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


class RoomInterpretationForm(forms.ModelForm):
    """Per-room interpretation configuration.

    ``target_languages`` is stored as a JSON list but edited as a
    comma-separated string for convenience.
    """

    target_languages = forms.CharField(
        required=False,
        label=_("Target languages"),
        help_text=_("Comma-separated language codes to translate into, e.g. de, fr."),
        widget=forms.TextInput(attrs={"placeholder": "de, fr, es"}),
    )

    class Meta:
        model = RoomInterpretation
        fields = [
            "hls_url",
            "source_language",
            "target_languages",
            "transcription_provider",
            "translation_provider",
        ]
        widgets = {
            "hls_url": forms.URLInput(
                attrs={"placeholder": "https://stream.example.com/room.m3u8"}
            ),
            "source_language": forms.TextInput(attrs={"placeholder": "en"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and isinstance(self.instance.target_languages, list):
            self.initial["target_languages"] = ", ".join(self.instance.target_languages)

    def clean_target_languages(self):
        raw = self.cleaned_data.get("target_languages") or ""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        seen = set()
        result = []
        for code in codes:
            if code not in seen:
                seen.add(code)
                result.append(code)
        return result
