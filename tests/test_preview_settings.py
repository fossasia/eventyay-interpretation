"""Tests for caption preview settings form and helpers."""

from interpretation.forms import (
    CaptionPreviewSettingsForm,
    preview_settings_payload,
)


def test_preview_settings_payload_maps_language_to_list():
    form = CaptionPreviewSettingsForm(
        data={
            "transcription_provider": "whisper_local",
            "translation_provider": "nllb_local",
            "target_language": "de",
        }
    )
    assert form.is_valid(), form.errors
    assert preview_settings_payload(form) == {
        "transcription_provider": "whisper_local",
        "translation_provider": "nllb_local",
        "target_languages": ["de"],
    }


def test_preview_settings_form_requires_providers():
    form = CaptionPreviewSettingsForm(
        data={
            "transcription_provider": "",
            "translation_provider": "",
            "target_language": "",
        }
    )
    assert not form.is_valid()
