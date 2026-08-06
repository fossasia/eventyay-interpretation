"""Tests for interpretation.settings and backend credential helpers."""

from interpretation.backend_credentials import (
    SUSI_AUTH_TOKEN,
    SUSI_BASE_URL,
    get_susi_auth_token,
    get_susi_base_url,
    get_susi_client,
    is_susi_configured,
)
from interpretation.settings import SETTING_IS_ENABLED, is_interpretation_enabled


class _FakeInterpretation:
    def __init__(self, config=None):
        self.backend_config = dict(config or {})
        self.saved = False

    def save(self, update_fields=None):
        self.saved = True


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


def test_is_interpretation_enabled_defaults_true():
    assert is_interpretation_enabled(_FakeEvent()) is True


def test_is_interpretation_enabled_reads_event_setting():
    event = _FakeEvent({SETTING_IS_ENABLED: False})
    assert is_interpretation_enabled(event) is False


def test_get_susi_client_uses_room_credentials():
    interpretation = _FakeInterpretation(
        {
            SUSI_BASE_URL: "https://example.com",
            SUSI_AUTH_TOKEN: "tok",
        }
    )
    client = get_susi_client(interpretation)
    assert client.base_url == "https://example.com/"
    assert client.auth_token == "tok"


def test_is_susi_configured_requires_url_and_token():
    assert is_susi_configured(None) is False
    assert (
        is_susi_configured(_FakeInterpretation({SUSI_BASE_URL: "https://example.com"}))
        is False
    )
    assert (
        is_susi_configured(
            _FakeInterpretation(
                {
                    SUSI_BASE_URL: "https://example.com",
                    SUSI_AUTH_TOKEN: "tok",
                }
            )
        )
        is True
    )
    assert (
        get_susi_base_url(_FakeInterpretation({SUSI_BASE_URL: "https://example.com/"}))
        == "https://example.com"
    )
    assert get_susi_auth_token(_FakeInterpretation({SUSI_AUTH_TOKEN: "tok"})) == "tok"
