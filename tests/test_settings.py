"""Tests for plugin settings."""

from interpretation.settings import (
    SETTING_IS_ENABLED,
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_BASE_URL,
    SETTING_USE_PLUGIN_STREAMS,
    get_interpretation_settings,
    get_susi_auth_token,
    get_susi_base_url,
    is_interpretation_enabled,
)


class _FakeEvent:
    id = 1
    pk = 1

    def __int__(self):
        return self.id

    def __init__(self, settings=None):
        self.settings = settings or {}


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None, as_type=None):
        val = self._data.get(key, default)
        if as_type is bool:
            return str(val).lower() in ("true", "1", "yes")
        return val


def test_is_interpretation_enabled_default():
    assert is_interpretation_enabled(_FakeEvent()) is True


def test_is_interpretation_enabled_false():
    event = _FakeEvent(_FakeSettings({SETTING_IS_ENABLED: False}))
    assert is_interpretation_enabled(event) is False


def test_get_interpretation_settings():
    event = _FakeEvent(
        _FakeSettings(
            {
                SETTING_IS_ENABLED: True,
                SETTING_USE_PLUGIN_STREAMS: False,
            }
        )
    )
    config = get_interpretation_settings(event)
    assert config["interpretation_is_enabled"] is True
    assert config["interpretation_use_plugin_streams"] is False


def test_get_susi_credentials():
    assert (
        get_susi_base_url(_FakeEvent(_FakeSettings({SETTING_SUSI_BASE_URL: "https://example.com/"})))
        == "https://example.com"
    )
    assert get_susi_auth_token(_FakeEvent(_FakeSettings({SETTING_SUSI_AUTH_TOKEN: "tok"}))) == "tok"
