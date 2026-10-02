from django import forms
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from eventyay.base.forms import SettingsForm

from .settings import (
    SETTING_IS_ENABLED,
    is_interpretation_enabled,
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
    from .models import VoxbentoOAuthGrant

    grant = VoxbentoOAuthGrant.objects.filter(event=event).first()
    if grant:
        messages.success(
            request,
            _("Successfully connected to VoxBento at %(server)s via OAuth 2.0.")
            % {"server": voxbento_server_host(event)},
        )
        return

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
            _("Successfully connected to VoxBento at %(server)s.") % {"server": voxbento_server_host(event)},
        )


class VoxbentoInterpreterCredentialsForm(forms.Form):
    """Event-level VoxBento credentials fields."""

    interpretation_voxbento_base_url = forms.URLField(
        label=_("VoxBento Base URL"),
        help_text=_("Base URL of the VoxBento Console, e.g. https://voxbento.example.com"),
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
            self.fields["interpretation_voxbento_base_url"].initial = get_voxbento_base_url(event)

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
            raise forms.ValidationError(_("Both Base URL and API Key are required to connect to VoxBento."))
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
            _("Connected to VoxBento at %(server)s.") % {"server": voxbento_server_host(event)},
        )
        return True


def room_form_prefix(room_id: int) -> str:
    return f"room-{room_id}"


import os


def get_language_choices():
    map_path = os.path.join(os.path.dirname(__file__), "language_map.yml")
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            choices = [("", _("Select a Language..."))]
            for line in lines:
                if ":" in line:
                    lang, code = line.split(":", 1)
                    lang = lang.strip()
                    code = code.strip().strip("'\"")
                    choices.append((code, f"{lang} ({code})"))
            return choices
    except Exception:
        return [("", _("Select a Language...")), ("en", "English (en)")]


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
    enable_transcription = forms.BooleanField(
        label=_("Enable AI Transcription for Event Audio"),
        required=False,
    )
    transcription_provider = forms.ChoiceField(
        label=_("Transcription Provider"),
        required=False,
        choices=[
            ("", _("Select a Provider...")),
            ("local", _("Local (Faster-Whisper)")),
            ("openai", _("OpenAI")),
            ("deepgram", _("Deepgram")),
            ("nvidia", _("NVIDIA")),
            ("elevenlabs", _("ElevenLabs")),
        ],
    )
    transcription_model = forms.ChoiceField(
        label=_("Transcription Model"),
        required=False,
        choices=[
            ("", _("Select a Model...")),
            (
                _("Local"),
                (
                    ("tiny", "tiny (Fastest)"),
                    ("base", "base"),
                ),
            ),
            (
                _("OpenAI"),
                (
                    ("whisper-1", "whisper-1"),
                    ("gpt-4o-realtime-preview", "gpt-4o-realtime-preview"),
                    ("gpt-4o-mini-realtime-preview", "gpt-4o-mini-realtime-preview"),
                ),
            ),
            (_("Deepgram"), (("nova-2", "nova-2"),)),
            (
                _("NVIDIA"),
                (
                    ("parakeet-rnnt", "parakeet-rnnt"),
                    ("parakeet-ctc", "parakeet-ctc"),
                ),
            ),
            (_("ElevenLabs"), (("scribe_v2_realtime", "scribe_v2_realtime"),)),
        ],
    )
    source_language = forms.ChoiceField(
        label=_("Event Language"),
        required=False,
        choices=[],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    enable_translation = forms.BooleanField(
        label=_("Enable Real-Time LLM Translation for Event Audio"),
        required=False,
    )
    translation_provider = forms.ChoiceField(
        label=_("Translation Provider"),
        required=False,
    )
    translation_model = forms.ChoiceField(
        label=_("Translation Model"),
        required=False,
    )

    openai_api_key = forms.CharField(
        label=_("OpenAI API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    deepgram_api_key = forms.CharField(
        label=_("Deepgram API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    nvidia_api_key = forms.CharField(
        label=_("NVIDIA Parakeet API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    elevenlabs_api_key = forms.CharField(
        label=_("ElevenLabs API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    translation_openai_api_key = forms.CharField(
        label=_("OpenAI API Key (Translation)"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    openrouter_api_key = forms.CharField(
        label=_("OpenRouter API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    gemini_api_key = forms.CharField(
        label=_("Google Gemini API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    anthropic_api_key = forms.CharField(
        label=_("Anthropic API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )
    groq_api_key = forms.CharField(
        label=_("Groq API Key"), required=False, widget=forms.PasswordInput(render_value=False)
    )

    def __init__(self, *args, event=None, invalid_api_keys=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.invalid_api_keys = invalid_api_keys or {}
        from .backends import list_available_interpreters

        interpreters = list_available_interpreters(event)
        self.fields["interpreter"].choices = [(item["id"], item["label"]) for item in interpreters]
        self.fields["source_language"].choices = get_language_choices()
        self.fields["translation_provider"].choices = [
            ("", _("Select a Provider...")),
            ("local", _("Local LLM")),
            ("openai", _("OpenAI")),
            ("openrouter", _("OpenRouter")),
            ("gemini", _("Google Gemini")),
            ("anthropic", _("Anthropic")),
            ("groq", _("Groq")),
        ]
        self.fields["translation_model"].choices = [
            ("", _("Select a Model...")),
            (_("Local"), (("nllb-200-distilled-600M", "nllb-200-distilled-600M"),)),
            (
                _("OpenAI"),
                (
                    ("gpt-4o", "gpt-4o"),
                    ("gpt-4o-mini", "gpt-4o-mini"),
                    ("gpt-4-turbo", "gpt-4-turbo"),
                    ("gpt-4o-mini-realtime-preview", "gpt-4o-mini-realtime-preview"),
                ),
            ),
            (
                _("OpenRouter"),
                (
                    ("meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-3.3-70b-instruct"),
                    ("google/gemini-flash-1.5", "google/gemini-flash-1.5"),
                    ("anthropic/claude-3.5-sonnet", "anthropic/claude-3.5-sonnet"),
                ),
            ),
            (
                _("Google Gemini"),
                (
                    ("gemini-2.5-flash", "gemini-2.5-flash"),
                    ("gemini-2.5-pro", "gemini-2.5-pro"),
                    ("gemini-1.5-flash", "gemini-1.5-flash"),
                ),
            ),
            (
                _("Anthropic"),
                (
                    ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20241022"),
                    ("claude-3-5-haiku-20241022", "claude-3-5-haiku-20241022"),
                ),
            ),
            (
                _("Groq"),
                (
                    ("openai/gpt-oss-120b", "openai/gpt-oss-120b"),
                    ("openai/gpt-oss-20b", "openai/gpt-oss-20b"),
                    ("groq/compound-mini", "groq/compound-mini"),
                ),
            ),
        ]
        for name, field in self.fields.items():
            if not isinstance(field, forms.BooleanField):
                field.widget.attrs.setdefault("class", "form-control")

        grant = None
        if self.event:
            from .models import VoxbentoOAuthGrant

            grant = VoxbentoOAuthGrant.objects.filter(event=self.event).first()

        self.configured_api_keys = {
            "openai": bool(grant and grant.openai_api_key),
            "deepgram": bool(grant and grant.deepgram_api_key),
            "nvidia": bool(grant and grant.nvidia_api_key),
            "elevenlabs": bool(grant and grant.elevenlabs_api_key),
            "translation_openai": bool(grant and grant.translation_openai_api_key),
            "openrouter": bool(grant and grant.openrouter_api_key),
            "gemini": bool(grant and grant.gemini_api_key),
            "anthropic": bool(grant and grant.anthropic_api_key),
            "groq": bool(grant and grant.groq_api_key),
        }

    def clean(self):
        cleaned_data = super().clean()
        interpreter = cleaned_data.get("interpreter")
        room_enabled = cleaned_data.get("room_enabled", False)
        if interpreter == "voxbento" and room_enabled:
            from .backends.voxbento_credentials import get_voxbento_base_url

            try:
                base_url = get_voxbento_base_url(self.event)
            except Exception:
                base_url = None

            from .models import VoxbentoOAuthGrant

            grant = VoxbentoOAuthGrant.objects.filter(event=self.event).first()
            has_active_grant = (
                grant and not getattr(grant, "is_disconnected", False) and bool(getattr(grant, "access_token", ""))
            )

            if not (base_url and has_active_grant):
                interpreter_label = dict(self.fields["interpreter"].choices).get(interpreter, interpreter)
                self.add_error(
                    "interpreter",
                    _("Please connect to %(service)s before configuring this room.") % {"service": interpreter_label},
                )

            # Validation for AI Interpretation fields
            enable_transcription = cleaned_data.get("enable_transcription", False)
            if enable_transcription:
                provider = cleaned_data.get("transcription_provider")
                if not provider:
                    self.add_error("transcription_provider", _("This field is required when transcription is enabled."))
                elif provider != "local":
                    key_map_name = provider
                    api_key = cleaned_data.get(f"{key_map_name}_api_key")
                    if not api_key and not self.configured_api_keys.get(key_map_name):
                        self.add_error(f"{key_map_name}_api_key", _("API key is required for this provider."))

                if not cleaned_data.get("transcription_model"):
                    self.add_error("transcription_model", _("This field is required when transcription is enabled."))

            enable_translation = cleaned_data.get("enable_translation", False)
            if enable_translation:
                if not enable_transcription:
                    self.add_error(
                        "enable_translation", _("Floor Audio Transcription must be enabled to use translation.")
                    )
                provider = cleaned_data.get("translation_provider")
                if not provider:
                    self.add_error("translation_provider", _("This field is required when translation is enabled."))
                elif provider != "local":
                    key_map_name = provider if provider != "openai" else "translation_openai"
                    api_key = cleaned_data.get(f"{key_map_name}_api_key")
                    if not api_key and not self.configured_api_keys.get(key_map_name):
                        self.add_error(f"{key_map_name}_api_key", _("API key is required for this provider."))

                if not cleaned_data.get("translation_model"):
                    self.add_error("translation_model", _("This field is required when translation is enabled."))

        return cleaned_data


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
            "room module."
        ),
    )

    def save(self):
        was_enabled = is_interpretation_enabled(self.obj) if self.obj else True
        result = super().save()
        enable_key = f"{self.prefix}-{SETTING_IS_ENABLED}" if self.prefix else SETTING_IS_ENABLED
        settings_saved = enable_key in self.data or EVENT_SETTINGS_SAVE_KEY in self.data
        if self.obj and was_enabled and not is_interpretation_enabled(self.obj) and settings_saved:
            from .room_control import stop_all_event_sessions

            stop_all_event_sessions(self.obj)
        return result
