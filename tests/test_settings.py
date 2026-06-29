"""Tests for interpretation.settings helpers."""

from interpretation.settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
    get_auth_token,
    get_base_url,
    get_susi_client,
    is_interpretation_enabled,
)


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


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


def test_settings_helpers_read_event_values():
    event = _FakeEvent(
        {
            SETTING_BASE_URL: "https://example.com",
            SETTING_AUTH_TOKEN: "tok",
            SETTING_IS_ENABLED: True,
        }
    )
    assert get_base_url(event) == "https://example.com"
    assert get_auth_token(event) == "tok"
    assert is_interpretation_enabled(event) is True


def test_get_susi_client_uses_event_settings():
    event = _FakeEvent(
        {
            SETTING_BASE_URL: "https://example.com",
            SETTING_AUTH_TOKEN: "tok",
        }
    )
    client = get_susi_client(event)
    assert client.base_url == "https://example.com/"
    assert client.auth_token == "tok"
