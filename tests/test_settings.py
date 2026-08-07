"""Tests for event-level interpretation settings and credentials."""

from interpretation.interpreter_credentials import (
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_BASE_URL,
    get_susi_auth_token,
    get_susi_base_url,
    get_susi_client,
    is_susi_configured,
)
from tests.conftest import SUSI_EVENT_CREDENTIALS, apply_susi_event_credentials


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = settings or {}


class _FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, as_type=str):
        if key not in self._data:
            return default
        return as_type(self._data[key])

    def set(self, key, value):
        self._data[key] = value


def test_get_susi_client_uses_event_credentials():
    event = _FakeEvent(_FakeSettings(SUSI_EVENT_CREDENTIALS))
    client = get_susi_client(event)
    assert client.base_url == "https://susi.example.com"
    assert client.auth_token == "jwt-test-token"


def test_is_susi_configured_requires_url_and_token():
    assert is_susi_configured(None) is False
    event = _FakeEvent(_FakeSettings({SETTING_SUSI_BASE_URL: "https://example.com"}))
    assert is_susi_configured(event) is False
    event = _FakeEvent(_FakeSettings(SUSI_EVENT_CREDENTIALS))
    assert is_susi_configured(event) is True
    assert (
        get_susi_base_url(_FakeEvent(_FakeSettings({SETTING_SUSI_BASE_URL: "https://example.com/"})))
        == "https://example.com"
    )
    assert (
        get_susi_auth_token(_FakeEvent(_FakeSettings({SETTING_SUSI_AUTH_TOKEN: "tok"})))
        == "tok"
    )


def test_is_susi_configured_reads_event_settings(event):
    apply_susi_event_credentials(event)
    assert is_susi_configured(event) is True
    assert get_susi_base_url(event) == "https://susi.example.com"
