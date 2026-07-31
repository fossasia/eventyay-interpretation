"""Tests for InterpretationSettingsForm validation and connect flow."""

from interpretation.forms import (
    CONNECT_POST_KEY,
    InterpretationSettingsForm,
    RoomInterpretationForm,
)
from interpretation.settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    SETTING_SUSI_EMAIL,
)

PUBLIC_URL = "https://example.com"


_SETTING_TYPES = {
    SETTING_BASE_URL: str,
    SETTING_AUTH_TOKEN: str,
    SETTING_IS_ENABLED: bool,
}


class _FakeHierarkey:
    defaults = {}

    def get_declared_type(self, key):
        return _SETTING_TYPES.get(key, str)


class _FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})
        self._parent = None
        self._h = _FakeHierarkey()

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


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


def _form(data, settings=None, prefix="interpretation"):
    post = {}
    for key, value in data.items():
        if key == CONNECT_POST_KEY:
            post[key] = value
        else:
            post[f"{prefix}-{key}"] = value
    return InterpretationSettingsForm(
        obj=_FakeEvent(settings), data=post, prefix=prefix
    )


def test_base_url_trailing_slash_is_stripped():
    form = _form(
        {
            SETTING_BASE_URL: f"{PUBLIC_URL}/",
            SETTING_IS_ENABLED: False,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data[SETTING_BASE_URL] == PUBLIC_URL


def test_enabling_without_connection_is_rejected():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_IS_ENABLED: True,
        }
    )
    assert not form.is_valid()
    assert SETTING_IS_ENABLED in form.errors


def test_enabling_with_existing_token_is_accepted():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_IS_ENABLED: True,
        },
        settings={SETTING_AUTH_TOKEN: "stored-token"},
    )
    assert form.is_valid(), form.errors


def test_connect_requires_email_and_password():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            "susi_connect_email": "",
            "susi_connect_password": "",
            CONNECT_POST_KEY: "1",
        }
    )
    assert not form.is_valid()
    assert "susi_connect_email" in form.errors
    assert "susi_connect_password" in form.errors


def test_connect_with_credentials_is_valid(monkeypatch):
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
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            "susi_connect_email": "bot@example.com",
            "susi_connect_password": "secret",
            CONNECT_POST_KEY: "1",
        }
    )
    assert form.is_valid(), form.errors
    form.save_pending_connect()
    form.run_connect_action(request=type("R", (), {})())
    assert form.obj.settings.get(SETTING_AUTH_TOKEN) == "jwt"
    assert form.obj.settings.get(SETTING_SUSI_EMAIL) == "bot@example.com"


def test_failed_connect_does_not_enable_interpretation(monkeypatch):
    from django.contrib import messages

    from interpretation.susi import SusiError

    monkeypatch.setattr(messages, "success", lambda *a, **k: None)
    monkeypatch.setattr(messages, "error", lambda *a, **k: None)

    def fail_login(self, email, password):
        raise SusiError("Invalid credentials")

    monkeypatch.setattr("interpretation.forms.SusiClient.login", fail_login)
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            "susi_connect_email": "bot@example.com",
            "susi_connect_password": "wrong",
            SETTING_IS_ENABLED: True,
            CONNECT_POST_KEY: "1",
        }
    )
    assert form.is_valid(), form.errors
    form.save_pending_connect()
    form.run_connect_action(request=type("R", (), {})())
    enabled = form.obj.settings.get(SETTING_IS_ENABLED, default=False, as_type=bool)
    assert enabled is False
    assert not form.obj.settings.get(SETTING_AUTH_TOKEN)


def test_successful_connect_with_enable_sets_enabled_flag(monkeypatch):
    from django.contrib import messages

    from interpretation.susi import SusiLoginResult

    monkeypatch.setattr(messages, "success", lambda *a, **k: None)
    monkeypatch.setattr(messages, "error", lambda *a, **k: None)

    def fake_login(self, email, password):
        return SusiLoginResult(token="jwt", email=email, name="Bot")

    monkeypatch.setattr("interpretation.forms.SusiClient.login", fake_login)
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            "susi_connect_email": "bot@example.com",
            "susi_connect_password": "secret",
            SETTING_IS_ENABLED: True,
            CONNECT_POST_KEY: "1",
        }
    )
    assert form.is_valid(), form.errors
    form.save_pending_connect()
    form.run_connect_action(request=type("R", (), {})())
    assert form.obj.settings.get(SETTING_IS_ENABLED, as_type=bool) is True


def test_save_does_not_persist_connect_credentials():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            "susi_connect_email": "bot@example.com",
            "susi_connect_password": "secret",
            SETTING_IS_ENABLED: False,
        },
    )
    assert form.is_valid(), form.errors
    form.save()
    stored = form.obj.settings._data
    assert "susi_connect_password" not in stored
    assert "susi_connect_email" not in stored
    assert stored.get(SETTING_BASE_URL) == PUBLIC_URL


def test_room_form_parses_comma_separated_languages():
    form = RoomInterpretationForm(
        data={
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
            "stream_url": "",
            "target_languages": "",
            "transcription_provider": "",
            "translation_provider": "",
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["target_languages"] == []
