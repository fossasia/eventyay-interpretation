from django import forms
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SettingsForm

from .interpreter_credentials import (
    get_susi_account_email,
    get_susi_auth_token,
    get_susi_base_url,
    get_susi_client,
    is_susi_configured,
    save_susi_credentials,
    susi_account_label,
)
from .settings import SETTING_IS_ENABLED, SETTING_USE_PLUGIN_STREAMS, is_interpretation_enabled
from .susi import SusiClient, SusiError
from .susi_providers import (
    SUSI_TRANSCRIPTION_PROVIDERS,
    SUSI_TRANSLATION_PROVIDERS,
)

CONNECT_POST_KEY = "interpretation_connect"
TEST_POST_KEY = "interpretation_test_connection"
EVENT_SETTINGS_SAVE_KEY = "interpretation_event_settings_save"
INTERPRETER_ACTION_KEY = "interpretation_interpreter_action"
INTERPRETER_ID_KEY = "interpretation_interpreter_id"
ROOM_ID_KEY = "interpretation_room_id"
ROOM_ACTION_KEY = "interpretation_room_action"
PREVIEW_ACTION_KEY = "preview_action"
PREVIEW_SAVE = "save_settings"
PREVIEW_START = "start"
PREVIEW_STOP = "stop"

from .backends.voxbento_credentials import (
    VoxbentoError,
    get_voxbento_api_key,
    get_voxbento_base_url,
    is_voxbento_configured,
    save_voxbento_credentials,
    test_voxbento_connection,
    voxbento_server_host,
)


def verify_voxbento_connection(event, request) -> None:
    """Verify stored event-level VoxBento credentials."""
    base_url = get_voxbento_base_url(event)
    api_key = get_voxbento_api_key(event)
    if not base_url or not api_key:
        messages.error(
            request,
            _("Please provide a VoxBento Base URL and API Key before testing."),
        )
        return

    try:
        test_voxbento_connection(base_url, api_key, event.slug)
    except VoxbentoError as exc:
        messages.error(
            request,
            _("VoxBento connection failed: %(error)s") % {"error": str(exc)},
        )
    else:
        messages.success(
            request,
            _("Successfully connected to VoxBento at %(server)s.")
            % {"server": voxbento_server_host(event)},
        )


class VoxbentoInterpreterCredentialsForm(forms.Form):
    """Event-level VoxBento credentials fields."""

    interpretation_voxbento_base_url = forms.URLField(
        label=_("VoxBento Base URL"),
        help_text=_(
            "Base URL of the VoxBento Console, e.g. https://voxbento.example.com"
        ),
        required=False,
        widget=forms.URLInput(attrs={"placeholder": "https://voxbento.example.com"}),
    )
    interpretation_voxbento_api_key = forms.CharField(
        label=_("VoxBento API Key"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Event-scoped API Key generated from VoxBento."),
    )

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        for name in self.fields:
            self.fields[name].widget.attrs.setdefault("class", "form-control")
        if event and get_voxbento_base_url(event):
            self.fields[
                "interpretation_voxbento_base_url"
            ].initial = get_voxbento_base_url(event)

    @property
    def is_connected(self) -> bool:
        return is_voxbento_configured(self.event)

    @property
    def connected_label(self) -> str:
        return _("VoxBento API Key configured")

    def _connecting(self) -> bool:
        return CONNECT_POST_KEY in self.data

    def clean(self):
        cleaned_data = super().clean()
        if not self._connecting():
            return cleaned_data
        base_url = cleaned_data.get("interpretation_voxbento_base_url")
        api_key = cleaned_data.get("interpretation_voxbento_api_key")
        if not base_url or not api_key:
            raise forms.ValidationError(
                _("Both Base URL and API Key are required to connect to VoxBento.")
            )
        return cleaned_data

    def run_connect_action(self, request, event) -> bool:
        base_url = self.cleaned_data.get("interpretation_voxbento_base_url").strip()
        api_key = self.cleaned_data.get("interpretation_voxbento_api_key").strip()

        try:
            test_voxbento_connection(base_url, api_key, event.slug)
        except VoxbentoError as exc:
            messages.error(
                request,
                _("Could not connect to VoxBento: %(error)s") % {"error": str(exc)},
            )
            return False

        save_voxbento_credentials(event, base_url, api_key)
        messages.success(
            request,
            _("Connected to VoxBento at %(server)s.")
            % {"server": voxbento_server_host(event)},
        )
        return True


def verify_susi_connection(event, request) -> None:
    """Verify stored event-level SUSI credentials."""
    base_url = get_susi_base_url(event)
    if not base_url:
        messages.error(
            request,
            _("Sign in to SUSI with a server URL before testing."),
        )
        return
    token = get_susi_auth_token(event)
    if not token:
        messages.error(
            request,
            _("Sign in to SUSI before testing the connection."),
        )
        return
    client = get_susi_client(event)
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
    """Per-room interpreter selection."""

    interpreter = forms.ChoiceField(
        label=_("Interpreter"),
        required=True,
    )
    room_enabled = forms.BooleanField(
        label=_("Enable interpretation for this room"),
        required=False,
    )
    target_languages = forms.CharField(
        label=_("Target Languages (comma-separated codes, e.g. es, fr)"),
        required=False,
    )

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .backends import list_available_interpreters

        interpreters = list_available_interpreters(event)
        self.fields["interpreter"].choices = [
            (item["id"], item["label"]) for item in interpreters
        ]
        for name, field in self.fields.items():
            if name != "room_enabled":
                field.widget.attrs.setdefault("class", "form-control")


class SusiInterpreterCredentialsForm(forms.Form):
    """Event-level SUSI sign-in fields."""

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

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        for name in self.fields:
            self.fields[name].widget.attrs.setdefault("class", "form-control")
        if event and get_susi_account_email(event):
            self.fields["susi_connect_email"].initial = get_susi_account_email(event)
        if event and get_susi_base_url(event):
            self.fields["interpretation_base_url"].initial = get_susi_base_url(event)

    @property
    def is_connected(self) -> bool:
        return is_susi_configured(self.event)

    @property
    def connected_label(self) -> str:
        return susi_account_label(self.event)

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
            or get_susi_base_url(self.event)
            or ""
        )

    def run_connect_action(self, request, event) -> bool:
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
            return False
        save_susi_credentials(
            event,
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
        return True


class InterpretationSettingsForm(SettingsForm):
    """Event-level interpretation toggle."""

    interpretation_is_enabled = forms.BooleanField(
        label=_("Enable live interpretation for this event"),
        required=False,
    )
    interpretation_use_plugin_streams = forms.BooleanField(
        label=_("Use plugin language streams in the video room"),
        required=False,
        help_text=_(
            "When enabled, the video room audio translation dropdown reads "
            "language streams from this plugin instead of the core video "
            "room module. Core language URLs are kept but hidden."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("interpretation_is_enabled", "interpretation_use_plugin_streams"):
            self.fields[name].widget.attrs.setdefault("class", "form-control")

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


def language_streams_form_prefix(room_id: int) -> str:
    return f"room-{room_id}-streams"


def parse_language_streams_post(post, prefix: str):
    from .language_streams import validate_language_streams

    count = int(post.get(f"{prefix}-count") or 0)
    entries = []
    for index in range(count):
        language = (post.get(f"{prefix}-{index}-language") or "").strip()
        audio_source = (post.get(f"{prefix}-{index}-audio_source") or "").strip()
        use_video = post.get(f"{prefix}-{index}-use_video") == "on"
        if not language and not audio_source:
            continue
        entries.append(
            {
                "language": language,
                "youtube_id": audio_source,
                "use_video": use_video,
            }
        )
    return validate_language_streams(entries)


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
