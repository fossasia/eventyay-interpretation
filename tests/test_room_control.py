"""Tests for room_control helpers and language normalization."""

import pytest

from interpretation.models import RoomInterpretation
from interpretation.room_control import clear_room_interpretation_setup
from interpretation.utils import (
    normalize_target_languages,
    validate_target_language_codes,
)
from tests.conftest import SUSI_BACKEND_CONFIG

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


def test_clear_room_setup_removes_credentials(event, room):
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        backend_config=dict(SUSI_BACKEND_CONFIG),
    )

    clear_room_interpretation_setup(room, event)

    interpretation.refresh_from_db()
    assert interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE
    assert interpretation.room_enabled is False
    assert not interpretation.backend_config.get("susi_auth_token")
