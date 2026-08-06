"""Tests for caption preview settings form and helpers."""

from interpretation.forms import (
    CaptionPreviewSettingsForm,
    preview_settings_payload,
)


def test_preview_settings_payload_maps_providers():
    form = CaptionPreviewSettingsForm(
        data={
            "transcription_provider": "whisper_local",
            "translation_provider": "nllb_local",
        }
    )
    assert form.is_valid(), form.errors
    assert preview_settings_payload(form) == {
        "transcription_provider": "whisper_local",
        "translation_provider": "nllb_local",
    }


def test_preview_settings_form_requires_providers():
    form = CaptionPreviewSettingsForm(
        data={
            "transcription_provider": "",
            "translation_provider": "",
        }
    )
    assert not form.is_valid()
