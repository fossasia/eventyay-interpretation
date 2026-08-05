from django import forms
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SettingsForm

from .models import RoomInterpretation
from .settings import (
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    disconnect_susi,
    get_auth_token,
    get_base_url,
    get_susi_email,
    get_susi_name,
    is_interpretation_enabled,
    save_susi_connection,
)
from .susi import SusiClient, SusiError
from .susi_providers import (
    CAPTION_LANGUAGE_CHOICES,
    SUSI_TRANSCRIPTION_PROVIDERS,
    SUSI_TRANSLATION_PROVIDERS,
)

CONNECT_POST_KEY = "interpretation_connect"
DISCONNECT_POST_KEY = "interpretation_disconnect"
TEST_POST_KEY = "interpretation_test_connection"
EVENT_SETTINGS_SAVE_KEY = "interpretation_event_settings_save"
ROOM_ID_KEY = "interpretation_room_id"
ROOM_ACTION_KEY = "interpretation_room_action"
PREVIEW_ACTION_KEY = "preview_action"
PREVIEW_SAVE = "save_settings"
PREVIEW_START = "start"
PREVIEW_STOP = "stop"


def verify_susi_connection(event, request) -> None:
    """Verify stored SUSI credentials for ``event``."""
    base_url = get_base_url(event)
    if not base_url:
        messages.error(
            request,
            _("Sign in to SUSI with a server URL before testing the connection."),
        )
        return
    token = get_auth_token(event)
    if not token:
        messages.error(
            request,
            _("Sign in to SUSI before testing the connection."),
        )
        return
    client = SusiClient(base_url, token)
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


def disconnect_susi_account(event, request) -> None:
    disconnect_susi(event)
    messages.success(request, _("Disconnected from SUSI."))


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

        interpreters = list_available_interpreters(event) if event else []
        self.fields["interpreter"].choices = [
            (item["id"], item["label"]) for item in interpreters
        ]
        for name, field in self.fields.items():
            if name != "room_enabled":
                field.widget.attrs.setdefault("class", "form-control")


class InterpretationSettingsForm(SettingsForm):
    """Commons dashboard form: connect to SUSI with email/password."""

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
    susi_connect_email = forms.EmailField(
        label=_("SUSI account email"),
        required=False,
    )
    susi_connect_password = forms.CharField(
        label=_("Password"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )
    interpretation_is_enabled = forms.BooleanField(
        label=_("Enable live interpretation for this event"),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "interpretation_base_url",
            "susi_connect_email",
            "susi_connect_password",
            "interpretation_is_enabled",
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

    _TRANSIENT_FIELDS = frozenset({"susi_connect_password", "susi_connect_email"})

    def _save_excluding_fields(self, excluded: frozenset):
        removed = {
            name: self.fields.pop(name) for name in excluded if name in self.fields
        }
        try:
            return super().save()
        finally:
            self.fields.update(removed)

    def save(self):
        # ponytail: login fields are POST-only; never write them to event.settings.
        was_enabled = is_interpretation_enabled(self.obj) if self.obj else True
        # ponytail: empty POST must not wipe a stored SUSI URL (e.g. Test button).
        url = (self.cleaned_data.get(SETTING_BASE_URL) or "").strip()
        excluded = set(self._TRANSIENT_FIELDS)
        if not url and get_base_url(self.obj):
            excluded.add(SETTING_BASE_URL)
        result = self._save_excluding_fields(frozenset(excluded))
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

    def save_pending_connect(self):
        """Persist URL before login; defer is_enabled until connect succeeds."""
        excluded = self._TRANSIENT_FIELDS | {SETTING_IS_ENABLED}
        return self._save_excluding_fields(excluded)

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
        self.obj.settings.set(
            SETTING_IS_ENABLED, bool(self.cleaned_data.get(SETTING_IS_ENABLED))
        )
        label = result.name or result.email
        if result.name and result.email:
            label = f"{result.name} ({result.email})"
        messages.success(
            request,
            _("Connected to SUSI as %(account)s.") % {"account": label},
        )

    def run_disconnect_action(self, request):
        disconnect_susi_account(self.obj, request)

    def run_test_action(self, request):
        verify_susi_connection(self.obj, request)


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
    target_language = forms.ChoiceField(
        label=_("Caption language"),
        choices=[("", _("— Select —"))] + list(CAPTION_LANGUAGE_CHOICES),
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
            targets = list(interpretation.target_languages or [])
            if targets:
                self.fields["target_language"].initial = targets[0]


def preview_settings_payload(form: CaptionPreviewSettingsForm) -> dict:
    language = (form.cleaned_data.get("target_language") or "").strip()
    return {
        "transcription_provider": form.cleaned_data["transcription_provider"],
        "translation_provider": form.cleaned_data["translation_provider"],
        "target_languages": [language] if language else [],
    }


class RoomInterpretationForm(forms.ModelForm):
    """Per-room interpretation configuration.

    ``target_languages`` is stored as a JSON list but edited as a
    comma-separated string for convenience.
    """

    target_languages = forms.CharField(
        required=False,
        label=_("Caption languages"),
        help_text=_(
            "Comma-separated language codes attendees can read captions in, "
            "e.g. de, fr."
        ),
        widget=forms.TextInput(attrs={"placeholder": "de, fr, es"}),
    )

    class Meta:
        model = RoomInterpretation
        fields = [
            "interpreter",
            "room_enabled",
            "stream_url",
            "target_languages",
            "transcription_provider",
            "translation_provider",
        ]
        widgets = {
            "stream_url": forms.URLInput(
                attrs={
                    "placeholder": (
                        "https://www.youtube.com/watch?v=… or https://…/stream.m3u8"
                    )
                }
            ),
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
