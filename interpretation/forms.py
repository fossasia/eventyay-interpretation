from django import forms
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SettingsForm

from .settings import (
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    disconnect_susi,
    get_auth_token,
    get_base_url,
    get_susi_email,
    get_susi_name,
    save_susi_connection,
)
from .susi import SusiClient, SusiError

CONNECT_POST_KEY = "interpretation_connect"
DISCONNECT_POST_KEY = "interpretation_disconnect"
TEST_POST_KEY = "interpretation_test_connection"


class InterpretationAdminForm(SettingsForm):
    """Video admin form: connect to SUSI with email/password, no manual JWT."""

    title = _("Interpretation (SUSI)")
    template = "interpretation/video_admin_settings.html"
    connect_action_post_key = CONNECT_POST_KEY
    disconnect_action_post_key = DISCONNECT_POST_KEY
    test_action_post_key = TEST_POST_KEY

    interpretation_base_url = forms.URLField(
        label=_("SUSI server URL"),
        help_text=_(
            "Base URL of the SUSI Translator server, e.g. https://susi.example.com"
        ),
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://susi.example.com"}),
    )
    interpretation_is_enabled = forms.BooleanField(
        label=_("Enable interpretation"),
        help_text=_("Master switch for SUSI interpretation on this event."),
        required=False,
    )
    susi_connect_email = forms.EmailField(
        label=_("SUSI account email"),
        required=False,
    )
    susi_connect_password = forms.CharField(
        label=_("Password"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "interpretation_base_url",
            "susi_connect_email",
            "susi_connect_password",
        ):
            self.fields[name].widget.attrs.setdefault("class", "form-control")
        if self.obj and get_susi_email(self.obj):
            self.fields["susi_connect_email"].initial = get_susi_email(self.obj)

    @property
    def is_connected(self) -> bool:
        return bool(self.obj and get_auth_token(self.obj))

    @property
    def connected_label(self) -> str:
        if not self.obj:
            return ""
        name = get_susi_name(self.obj)
        email = get_susi_email(self.obj)
        if name and email:
            return f"{name} ({email})"
        return email or name

    def _connecting(self) -> bool:
        return CONNECT_POST_KEY in self.data

    def clean_interpretation_base_url(self):
        url = (self.cleaned_data.get("interpretation_base_url") or "").strip()
        return url.rstrip("/")

    def clean(self):
        cleaned = super().clean()
        base_url = cleaned.get(SETTING_BASE_URL)
        email = (cleaned.get("susi_connect_email") or "").strip()
        password = cleaned.get("susi_connect_password") or ""

        if self._connecting():
            if not base_url:
                self.add_error(
                    SETTING_BASE_URL,
                    _("A SUSI server URL is required to connect."),
                )
            if not email:
                self.add_error(
                    "susi_connect_email",
                    _("Email is required to connect."),
                )
            if not password:
                self.add_error(
                    "susi_connect_password",
                    _("Password is required to connect."),
                )

        if cleaned.get(SETTING_IS_ENABLED):
            if not base_url:
                self.add_error(
                    SETTING_BASE_URL,
                    _("A SUSI server URL is required to enable interpretation."),
                )
            if not get_auth_token(self.obj) and not self._connecting():
                self.add_error(
                    SETTING_IS_ENABLED,
                    _("Connect to SUSI before enabling interpretation."),
                )
        return cleaned

    def run_connect_action(self, request):
        base_url = self.cleaned_data.get(SETTING_BASE_URL) or get_base_url(self.obj)
        email = (self.cleaned_data.get("susi_connect_email") or "").strip()
        password = self.cleaned_data.get("susi_connect_password") or ""
        client = SusiClient(base_url)
        try:
            result = client.login(email, password)
        except SusiError as exc:
            messages.error(
                request,
                _("Could not connect to SUSI: %(error)s") % {"error": str(exc)},
            )
            return
        save_susi_connection(
            self.obj,
            token=result.token,
            email=result.email,
            name=result.name,
        )
        label = result.name or result.email
        if result.name and result.email:
            label = f"{result.name} ({result.email})"
        messages.success(
            request,
            _("Connected to SUSI as %(account)s.") % {"account": label},
        )

    def run_disconnect_action(self, request):
        disconnect_susi(self.obj)
        messages.success(request, _("Disconnected from SUSI."))

    def run_test_action(self, request):
        base_url = self.cleaned_data.get(SETTING_BASE_URL) or get_base_url(self.obj)
        token = get_auth_token(self.obj)
        if not token:
            messages.error(
                request,
                _("Connect to SUSI before testing the connection."),
            )
            return
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
