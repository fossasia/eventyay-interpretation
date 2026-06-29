"""Tests for the InterpretationSettingsForm validation logic."""

from interpretation.forms import InterpretationSettingsForm, RoomInterpretationForm
from interpretation.settings import (
    SETTING_AUTH_TOKEN,
    SETTING_BASE_URL,
    SETTING_IS_ENABLED,
)

PUBLIC_URL = "https://example.com"


class _FakeHierarkey:
    defaults = {}

    def get_declared_type(self, key):
        return str


class _FakeSettings:
    _parent = None
    _h = _FakeHierarkey()

    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None, as_type=str):
        if key not in self._data:
            return default
        value = self._data[key]
        if as_type is bool:
            return bool(value)
        return as_type(value)

    def _cache(self):
        return self._data.keys()

    def freeze(self):
        return self._data.copy()

    def set(self, key, value):
        self._data[key] = value


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


def _form(data, settings=None):
    return InterpretationSettingsForm(obj=_FakeEvent(settings), data=data)


def test_base_url_trailing_slash_is_stripped():
    form = _form(
        {
            SETTING_BASE_URL: f"{PUBLIC_URL}/",
            SETTING_AUTH_TOKEN: "",
            SETTING_IS_ENABLED: False,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data[SETTING_BASE_URL] == PUBLIC_URL


def test_enabling_without_token_is_rejected():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_AUTH_TOKEN: "",
            SETTING_IS_ENABLED: True,
        }
    )
    assert not form.is_valid()
    assert SETTING_AUTH_TOKEN in form.errors


def test_enabling_with_token_is_accepted():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_AUTH_TOKEN: "tok",
            SETTING_IS_ENABLED: True,
        }
    )
    assert form.is_valid(), form.errors


def test_enabling_with_existing_token_and_redacted_input_is_accepted():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_AUTH_TOKEN: "*****",
            SETTING_IS_ENABLED: True,
        },
        settings={SETTING_AUTH_TOKEN: "stored-token"},
    )
    assert form.is_valid(), form.errors


def test_redacted_token_is_resolved_to_stored_value():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_AUTH_TOKEN: "*****",
            SETTING_IS_ENABLED: False,
        },
        settings={SETTING_AUTH_TOKEN: "stored-token"},
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data[SETTING_AUTH_TOKEN] == "stored-token"


def test_new_token_is_stripped():
    form = _form(
        {
            SETTING_BASE_URL: PUBLIC_URL,
            SETTING_AUTH_TOKEN: " tok ",
            SETTING_IS_ENABLED: False,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data[SETTING_AUTH_TOKEN] == "tok"


def test_room_form_parses_comma_separated_languages():
    form = RoomInterpretationForm(
        data={
            "hls_url": "https://stream.example.com/r.m3u8",
            "source_language": "en",
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
            "hls_url": "",
            "source_language": "",
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
            "hls_url": "",
            "source_language": "",
            "target_languages": "",
            "transcription_provider": "",
            "translation_provider": "",
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["target_languages"] == []
