"""Tests for event-level and per-room interpretation forms."""

from interpretation.forms import (
    CONNECT_POST_KEY,
    InterpretationSettingsForm,
    SusiInterpreterCredentialsForm,
)
from interpretation.interpreter_credentials import (
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_BASE_URL,
    get_susi_auth_token,
    get_susi_base_url,
    is_susi_configured,
)
from interpretation.settings import SETTING_IS_ENABLED

PUBLIC_URL = "https://example.com"


class _FakeHierarkey:
    defaults = {}

    def get_declared_type(self, key):
        return bool if key == SETTING_IS_ENABLED else str


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


def _event_form(data, settings=None, prefix="interpretation"):
    post = {f"{prefix}-{key}": value for key, value in data.items()}
    return InterpretationSettingsForm(
        obj=_FakeEvent(settings), data=post, prefix=prefix
    )


def _susi_credentials_form(data, event=None):
    post = {}
    for key, value in data.items():
        if key == CONNECT_POST_KEY:
            post[key] = value
        else:
            post[key] = value
    return SusiInterpreterCredentialsForm(data=post, event=event)


def test_event_enable_toggle_can_be_saved(monkeypatch):
    monkeypatch.setattr(
        "interpretation.room_control.stop_all_event_sessions",
        lambda event: None,
    )
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


def test_susi_credentials_base_url_trailing_slash_is_stripped():
    form = _susi_credentials_form({"interpretation_base_url": f"{PUBLIC_URL}/"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["interpretation_base_url"] == PUBLIC_URL


def test_susi_connect_requires_email_and_password():
    form = _susi_credentials_form(
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


def test_susi_connect_stores_credentials_on_event(monkeypatch):
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
    event = _FakeEvent()
    form = _susi_credentials_form(
        {
            "interpretation_base_url": PUBLIC_URL,
            "susi_connect_email": "bot@example.com",
            "susi_connect_password": "secret",
            CONNECT_POST_KEY: "1",
        },
        event=event,
    )
    assert form.is_valid(), form.errors
    form.run_connect_action(request=type("R", (), {})(), event=event)
    assert get_susi_auth_token(event) == "jwt"
    assert event.settings.get(SETTING_SUSI_BASE_URL) == PUBLIC_URL


def test_is_susi_configured_reads_event_settings():
    event = _FakeEvent(
        {
            SETTING_SUSI_BASE_URL: PUBLIC_URL,
            SETTING_SUSI_AUTH_TOKEN: "tok",
        }
    )
    assert is_susi_configured(event) is True
    assert get_susi_base_url(event) == PUBLIC_URL
