from django import forms
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SettingsForm

from .backend_credentials import (
    get_susi_account_email,
    get_susi_base_url,
    get_susi_auth_token,
    get_susi_client,
    is_susi_configured,
    save_susi_credentials,
    susi_account_label,
)
from .settings import SETTING_IS_ENABLED, is_interpretation_enabled
from .susi import SusiClient, SusiError
from .susi_providers import (
    SUSI_TRANSCRIPTION_PROVIDERS,
    SUSI_TRANSLATION_PROVIDERS,
)

CONNECT_POST_KEY = "interpretation_connect"
TEST_POST_KEY = "interpretation_test_connection"
EVENT_SETTINGS_SAVE_KEY = "interpretation_event_settings_save"
ROOM_ID_KEY = "interpretation_room_id"
ROOM_ACTION_KEY = "interpretation_room_action"
PREVIEW_ACTION_KEY = "preview_action"
PREVIEW_SAVE = "save_settings"
PREVIEW_START = "start"
PREVIEW_STOP = "stop"


def verify_susi_connection(interpretation, request) -> None:
    """Verify stored SUSI credentials for a room."""
    base_url = get_susi_base_url(interpretation)
    if not base_url:
        messages.error(
            request,
            _("Sign in to the interpreter with a server URL before testing."),
        )
        return
    token = get_susi_auth_token(interpretation)
    if not token:
        messages.error(
            request,
            _("Sign in to the interpreter before testing the connection."),
        )
        return
    client = get_susi_client(interpretation)
    try:
        result = client.verify()
    except SusiError as exc:
        messages.error(request, _("Connection failed: %(error)s") % {"error": str(exc)})
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


def room_form_prefix(room_id: int) -> str:
    return f"room-{room_id}"


class RoomConfigureForm(forms.Form):
    """Per-room interpreter and caption settings on the dashboard."""

    interpreter = forms.ChoiceField(
        label=_("Interpreter"),
        required=True,
    )
    room_enabled = forms.BooleanField(
        label=_("Enable interpretation for this room"),
        required=False,
    )

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .backends import list_available_interpreters

        interpreters = list_available_interpreters()
        self.fields["interpreter"].choices = [
            (item["id"], item["label"]) for item in interpreters
        ]
        for name, field in self.fields.items():
            if name != "room_enabled":
                field.widget.attrs.setdefault("class", "form-control")


class RoomSusiCredentialsForm(forms.Form):
    """Per-room SUSI sign-in fields."""

    interpretation_base_url = forms.URLField(
        label=_("SUSI server URL"),
        help_text=_(
            "Base URL of the SUSI Translator server, e.g. https://susi.example.com"
        ),
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://susi.example.com"}),
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

    def __init__(self, *args, interpretation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.interpretation = interpretation
        for name in self.fields:
            self.fields[name].widget.attrs.setdefault("class", "form-control")
        if interpretation and get_susi_account_email(interpretation):
            self.fields["susi_connect_email"].initial = get_susi_account_email(
                interpretation
            )
        if interpretation and get_susi_base_url(interpretation):
            self.fields["interpretation_base_url"].initial = get_susi_base_url(
                interpretation
            )

    @property
    def is_connected(self) -> bool:
        return is_susi_configured(self.interpretation)

    @property
    def connected_label(self) -> str:
        return susi_account_label(self.interpretation)

    def _connecting(self) -> bool:
        return CONNECT_POST_KEY in self.data

    def clean_interpretation_base_url(self):
        url = (self.cleaned_data.get("interpretation_base_url") or "").strip()
        return url.rstrip("/")

    def clean(self):
        cleaned = super().clean()
        base_url = cleaned.get("interpretation_base_url")
        email = (cleaned.get("susi_connect_email") or "").strip()
        password = cleaned.get("susi_connect_password") or ""

        if self._connecting():
            if not base_url:
                self.add_error(
                    "interpretation_base_url",
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
        return cleaned

    def resolved_base_url(self) -> str:
        return (
            self.cleaned_data.get("interpretation_base_url")
            or get_susi_base_url(self.interpretation)
            or ""
        )

    def run_connect_action(self, request, interpretation) -> None:
        base_url = self.resolved_base_url()
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
        save_susi_credentials(
            interpretation,
            base_url=base_url,
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


class InterpretationSettingsForm(SettingsForm):
    """Event-level interpretation toggle."""

    interpretation_is_enabled = forms.BooleanField(
        label=_("Enable live interpretation for this event"),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["interpretation_is_enabled"].widget.attrs.setdefault(
            "class", "form-control"
        )

    def save(self):
        was_enabled = is_interpretation_enabled(self.obj) if self.obj else True
        result = super().save()
        enable_key = (
            f"{self.prefix}-{SETTING_IS_ENABLED}" if self.prefix else SETTING_IS_ENABLED
        )
        settings_saved = enable_key in self.data or EVENT_SETTINGS_SAVE_KEY in self.data
        if (
            self.obj
            and was_enabled
            and not is_interpretation_enabled(self.obj)
            and settings_saved
        ):
            from .room_control import stop_all_event_sessions

            stop_all_event_sessions(self.obj)
        return result


class CaptionPreviewSettingsForm(forms.Form):
    """SUSI session settings for the temporary caption preview page."""

    transcription_provider = forms.ChoiceField(
        label=_("Transcription provider"),
        choices=[("", _("— Select —"))] + list(SUSI_TRANSCRIPTION_PROVIDERS),
        required=True,
    )
    translation_provider = forms.ChoiceField(
        label=_("Translation provider"),
        choices=[("", _("— Select —"))] + list(SUSI_TRANSLATION_PROVIDERS),
        required=True,
    )

    def __init__(self, *args, interpretation=None, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        if interpretation:
            if interpretation.transcription_provider:
                self.fields[
                    "transcription_provider"
                ].initial = interpretation.transcription_provider
            if interpretation.translation_provider:
                self.fields[
                    "translation_provider"
                ].initial = interpretation.translation_provider


def preview_settings_payload(form: CaptionPreviewSettingsForm) -> dict:
    return {
        "transcription_provider": form.cleaned_data["transcription_provider"],
        "translation_provider": form.cleaned_data["translation_provider"],
    }
