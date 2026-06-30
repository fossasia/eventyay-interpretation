"""Tests for InterpretationAdminForm validation and connect flow."""

from interpretation.forms import CONNECT_POST_KEY, InterpretationAdminForm
from interpretation.settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    SETTING_SUSI_EMAIL,
)

PUBLIC_URL = "https://example.com"


class _FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})

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
    return InterpretationAdminForm(
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
    form.run_connect_action(request=type("R", (), {})())
    assert form.obj.settings.get(SETTING_AUTH_TOKEN) == "jwt"
    assert form.obj.settings.get(SETTING_SUSI_EMAIL) == "bot@example.com"
