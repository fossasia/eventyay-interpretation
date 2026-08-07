"""Tests for room_control helpers and language normalization."""

import pytest

from interpretation.models import RoomInterpretation
from interpretation.room_control import (
    clear_room_interpretation_setup,
    serialize_room_interpretation,
    update_room_interpretation,
)
from interpretation.utils import (
    normalize_target_languages,
    validate_target_language_codes,
)
from tests.conftest import apply_susi_event_credentials

pytestmark = pytest.mark.django_db


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def test_normalize_target_languages_from_comma_string():
    assert normalize_target_languages("de, fr, de, es") == ["de", "fr", "es"]


def test_normalize_target_languages_from_list():
    assert normalize_target_languages(["de", "fr"]) == ["de", "fr"]


def test_normalize_target_languages_empty():
    assert normalize_target_languages("") == []
    assert normalize_target_languages([]) == []


def test_validate_target_language_codes_rejects_too_many():
    codes = ["a"] * 33
    with pytest.raises(ValueError, match="Too many"):
        validate_target_language_codes(codes)


def test_clear_room_setup_resets_room_without_touching_event_credentials(event, room):
    apply_susi_event_credentials(event)
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    clear_room_interpretation_setup(room, event)

    interpretation.refresh_from_db()
    assert interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE
    assert interpretation.room_enabled is False


def test_serialize_room_interpretation_reports_event_susi_status(event, room):
    apply_susi_event_credentials(event)
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    payload = serialize_room_interpretation(room, event, interpretation)

    assert payload["susi_connected"] is True
    assert "susi_auth_token" not in payload["backend_config"]


def test_merge_public_backend_config_strips_credential_keys(event, room):
    apply_susi_event_credentials(event)
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    update_room_interpretation(
        room,
        event,
        {
            "backend_config": {
                "susi_auth_token": "injected",
                "susi_base_url": "https://evil.example.com",
                "feature_flag": True,
            }
        },
    )

    interpretation.refresh_from_db()
    assert "susi_auth_token" not in interpretation.backend_config
    assert "susi_base_url" not in interpretation.backend_config
    assert interpretation.backend_config["feature_flag"] is True
