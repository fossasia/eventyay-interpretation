"""Tests for event-level and per-room interpretation forms."""

from interpretation.backend_credentials import (
    SUSI_AUTH_TOKEN,
    SUSI_BASE_URL,
    get_susi_auth_token,
    get_susi_base_url,
    is_susi_configured,
)
from interpretation.forms import (
    CONNECT_POST_KEY,
    InterpretationSettingsForm,
    RoomInterpretationForm,
    RoomSusiCredentialsForm,
)
from interpretation.models import RoomInterpretation
from interpretation.settings import SETTING_IS_ENABLED

PUBLIC_URL = "https://example.com"


class _FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})
        self._parent = None

    def get(self, key, default=None, as_type=str):
        if key not in self._data:
            return default
        value = self._data[key]
        if as_type is bool:
            return bool(value)
        return as_type(value)

    def freeze(self):
        return self._data.copy()

    def set(self, key, value):
        self._data[key] = value

    def _cache(self):
        return self._data.keys()

    class _h:
        defaults = {SETTING_IS_ENABLED: True}

        def get_declared_type(self, key):
            return bool if key == SETTING_IS_ENABLED else str

    _h = _h()


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


class _FakeInterpretation:
    def __init__(self, config=None):
        self.backend_config = dict(config or {})
        self.saved = False

    def save(self, update_fields=None):
        self.saved = True


def _event_form(data, settings=None, prefix="interpretation"):
    post = {f"{prefix}-{key}": value for key, value in data.items()}
    return InterpretationSettingsForm(
        obj=_FakeEvent(settings), data=post, prefix=prefix
    )


def _room_credentials_form(data, interpretation=None, prefix="room-1"):
    post = {}
    for key, value in data.items():
        if key == CONNECT_POST_KEY:
            post[key] = value
        else:
            post[f"{prefix}-{key}"] = value
    return RoomSusiCredentialsForm(
        data=post, prefix=prefix, interpretation=interpretation
    )


def test_event_enable_toggle_can_be_saved():
    form = _event_form({SETTING_IS_ENABLED: False})
    assert form.is_valid(), form.errors
    form.save()
    assert form.obj.settings.get(SETTING_IS_ENABLED, as_type=bool) is False


def test_event_disable_stops_sessions(monkeypatch):
    stopped = []

    def fake_stop_all(event):
        stopped.append(event)

    monkeypatch.setattr(
        "interpretation.room_control.stop_all_event_sessions",
        fake_stop_all,
    )
    form = _event_form(
        {SETTING_IS_ENABLED: False},
        settings={SETTING_IS_ENABLED: True},
    )
    assert form.is_valid(), form.errors
    form.save()
    assert stopped == [form.obj]


def test_room_credentials_base_url_trailing_slash_is_stripped():
    form = _room_credentials_form({"interpretation_base_url": f"{PUBLIC_URL}/"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["interpretation_base_url"] == PUBLIC_URL


def test_room_connect_requires_email_and_password():
    form = _room_credentials_form(
        {
            "interpretation_base_url": PUBLIC_URL,
            "susi_connect_email": "",
            "susi_connect_password": "",
            CONNECT_POST_KEY: "1",
        }
    )
    assert not form.is_valid()
    assert "susi_connect_email" in form.errors
    assert "susi_connect_password" in form.errors


def test_room_connect_stores_credentials_on_interpretation(monkeypatch):
    from django.contrib import messages

    from interpretation.susi import SusiLoginResult

    monkeypatch.setattr(messages, "success", lambda *a, **k: None)
    monkeypatch.setattr(messages, "error", lambda *a, **k: None)

    def fake_login(self, email, password):
        return SusiLoginResult(token="jwt", email=email, name="Bot")

    monkeypatch.setattr(
        "interpretation.forms.SusiClient.login",
        fake_login,
    )
    interpretation = _FakeInterpretation()
    form = _room_credentials_form(
        {
            "interpretation_base_url": PUBLIC_URL,
            "susi_connect_email": "bot@example.com",
            "susi_connect_password": "secret",
            CONNECT_POST_KEY: "1",
        },
        interpretation=interpretation,
    )
    assert form.is_valid(), form.errors
    form.run_connect_action(request=type("R", (), {})(), interpretation=interpretation)
    assert get_susi_auth_token(interpretation) == "jwt"
    assert interpretation.backend_config[SUSI_BASE_URL] == PUBLIC_URL


def test_room_form_parses_comma_separated_languages():
    form = RoomInterpretationForm(
        data={
            "interpreter": RoomInterpretation.INTERPRETER_NONE,
            "room_enabled": False,
            "stream_url": "https://stream.example.com/r.m3u8",
            "target_languages": "de, fr ,es",
            "transcription_provider": "",
            "translation_provider": "",
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["target_languages"] == ["de", "fr", "es"]


def test_room_form_deduplicates_languages():
    form = RoomInterpretationForm(
        data={
            "interpreter": RoomInterpretation.INTERPRETER_NONE,
            "room_enabled": False,
            "stream_url": "",
            "target_languages": "de, de, fr",
            "transcription_provider": "",
            "translation_provider": "",
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["target_languages"] == ["de", "fr"]


def test_room_form_empty_languages_is_empty_list():
    form = RoomInterpretationForm(
        data={
            "interpreter": RoomInterpretation.INTERPRETER_NONE,
            "room_enabled": False,
            "stream_url": "",
            "target_languages": "",
            "transcription_provider": "",
            "translation_provider": "",
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["target_languages"] == []


def test_is_susi_configured_reads_room_backend_config():
    interpretation = _FakeInterpretation(
        {
            SUSI_BASE_URL: PUBLIC_URL,
            SUSI_AUTH_TOKEN: "tok",
        }
    )
    assert is_susi_configured(interpretation) is True
    assert get_susi_base_url(interpretation) == PUBLIC_URL
