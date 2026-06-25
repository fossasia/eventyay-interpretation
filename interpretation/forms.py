from django import forms
from django.utils.translation import gettext_lazy as _

from .models import RoomInterpretation, SusiConnection


class SusiConnectionForm(forms.ModelForm):
    """Per-event SUSI server connection settings."""

    class Meta:
        model = SusiConnection
        fields = ["base_url", "auth_token", "is_enabled"]
        widgets = {
            "base_url": forms.URLInput(
                attrs={"placeholder": "https://susi.example.com"}
            ),
            "auth_token": forms.PasswordInput(
                render_value=True,
                attrs={"autocomplete": "off"},
            ),
        }

    def clean_base_url(self):
        url = (self.cleaned_data.get("base_url") or "").strip()
        return url.rstrip("/")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_enabled") and not cleaned.get("auth_token"):
            self.add_error(
                "auth_token",
                _("An authentication token is required to enable interpretation."),
            )
        return cleaned


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
        # Render the stored JSON list as a comma-separated string.
        if self.instance and isinstance(self.instance.target_languages, list):
            self.initial["target_languages"] = ", ".join(self.instance.target_languages)

    def clean_target_languages(self):
        raw = self.cleaned_data.get("target_languages") or ""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        # De-duplicate while preserving order.
        seen = set()
        result = []
        for code in codes:
            if code not in seen:
                seen.add(code)
                result.append(code)
        return result
