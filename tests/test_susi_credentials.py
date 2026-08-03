"""Tests for SUSI credential helpers used by the dashboard."""

from interpretation.forms import (
    InterpretationSettingsForm,
    verify_susi_connection,
)
from interpretation.settings import SETTING_AUTH_TOKEN, SETTING_BASE_URL
from interpretation.susi import SusiResult


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

    def set(self, key, value):
        self._data[key] = value

    def freeze(self):
        return self._data.copy()

    def _cache(self):
        return self._data.keys()

    class _h:
        defaults = {}

        def get_declared_type(self, key):
            return str

    _h = _h()


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


def test_test_susi_connection_uses_stored_credentials(monkeypatch):
    calls = []
    logged = []

    class FakeSusiClient:
        def __init__(self, base_url, auth_token="", timeout=10):
            calls.append((base_url, auth_token))

        def verify(self):
            return SusiResult(
                ok=True,
                status_code=200,
                data={"authenticated": True},
                message="Connected and authenticated.",
            )

    monkeypatch.setattr("interpretation.forms.SusiClient", FakeSusiClient)
    monkeypatch.setattr(
        "interpretation.forms.messages.success",
        lambda request, message: logged.append(message),
    )
    monkeypatch.setattr("interpretation.forms.messages.error", lambda *a, **k: None)

    event = _FakeEvent(
        {
            SETTING_BASE_URL: "https://susi.example.com",
            SETTING_AUTH_TOKEN: "jwt-test-token",
        }
    )
    verify_susi_connection(event, request=type("R", (), {})())
    assert calls == [("https://susi.example.com", "jwt-test-token")]
    assert logged


def test_save_does_not_wipe_stored_base_url_on_empty_post():
    event = _FakeEvent({SETTING_BASE_URL: "https://susi.example.com"})
    form = InterpretationSettingsForm(
        obj=event,
        data={"interpretation-interpretation_base_url": ""},
        prefix="interpretation",
    )
    assert form.is_valid(), form.errors
    form.save()
    assert event.settings.get(SETTING_BASE_URL) == "https://susi.example.com"
