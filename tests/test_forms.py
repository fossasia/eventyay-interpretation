"""Tests for the SusiConnectionForm validation logic (no database needed)."""

from interpretation.forms import RoomInterpretationForm, SusiConnectionForm


def test_base_url_trailing_slash_is_stripped():
    form = SusiConnectionForm(
        data={
            "base_url": "https://susi.example.com/",
            "auth_token": "",
            "is_enabled": False,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["base_url"] == "https://susi.example.com"


def test_enabling_without_token_is_rejected():
    form = SusiConnectionForm(
        data={
            "base_url": "https://susi.example.com",
            "auth_token": "",
            "is_enabled": True,
        }
    )
    assert not form.is_valid()
    assert "auth_token" in form.errors


def test_enabling_with_token_is_accepted():
    form = SusiConnectionForm(
        data={
            "base_url": "https://susi.example.com",
            "auth_token": "tok",
            "is_enabled": True,
        }
    )
    assert form.is_valid(), form.errors


def test_base_url_is_required():
    form = SusiConnectionForm(
        data={"base_url": "", "auth_token": "", "is_enabled": False}
    )
    assert not form.is_valid()
    assert "base_url" in form.errors


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
