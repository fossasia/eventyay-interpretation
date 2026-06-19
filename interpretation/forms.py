from django import forms
from django.utils.translation import gettext_lazy as _

from .models import SusiConnection


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
