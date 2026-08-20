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
        self.fields["interpreter"].choices = [(item["id"], item["label"]) for item in interpreters]
        for name, field in self.fields.items():
            if name != "room_enabled":
                field.widget.attrs.setdefault("class", "form-control")


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
