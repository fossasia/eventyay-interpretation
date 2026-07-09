"""Tests for attendee interpretation payload and room sync flags."""

from unittest.mock import patch

from interpretation.models import RoomInterpretation
from interpretation.room_control import attendee_interpretation_payload, update_room_interpretation


class FakeInterpretation:
    def __init__(self, *, room_enabled=False, status=RoomInterpretation.STATUS_IDLE, languages=None):
        self.room_enabled = room_enabled
        self.status = status
        self.target_languages = languages or []


def test_attendee_payload_disabled_when_room_off():
    interp = FakeInterpretation(room_enabled=False, status=RoomInterpretation.STATUS_RUNNING)
    assert attendee_interpretation_payload(interp, captions_url="https://x/captions/") is None


def test_attendee_payload_shell_when_room_on_session_idle():
    interp = FakeInterpretation(room_enabled=True, languages=["de", "fr"])
    payload = attendee_interpretation_payload(interp)
    assert payload == {
        "room_enabled": True,
        "enabled": False,
        "languages": ["de", "fr"],
        "url": "",
    }


def test_attendee_payload_live_when_session_running():
    interp = FakeInterpretation(room_enabled=True, status=RoomInterpretation.STATUS_RUNNING, languages=["de"])
    payload = attendee_interpretation_payload(interp, captions_url="https://x/captions/")
    assert payload == {
        "room_enabled": True,
        "enabled": True,
        "languages": ["de"],
        "url": "https://x/captions/",
    }


def test_attendee_payload_preserves_admin_language_order():
    interp = FakeInterpretation(room_enabled=True, languages=["hi", "en", "de"])
    payload = attendee_interpretation_payload(interp)
    assert payload["languages"] == ["hi", "en", "de"]


def test_update_room_interpretation_can_skip_attendee_sync():
    class FakeRoom:
        pk = 1

    class FakeEvent:
        pass

    interpretation = FakeInterpretation(room_enabled=True, languages=["de"])

    with patch(
        "interpretation.room_control.is_susi_connected", return_value=True
    ), patch(
        "interpretation.room_control.RoomInterpretation.objects.get_or_create",
        return_value=(interpretation, False),
    ) as get_or_create, patch(
        "interpretation.room_control.resync_attendee_interpretation"
    ) as resync:
        interpretation.save = lambda: None
        update_room_interpretation(
            FakeRoom(),
            FakeEvent(),
            {"target_languages": ["fr"]},
            sync_attendees=False,
        )
        resync.assert_not_called()
        assert interpretation.target_languages == ["fr"]
        get_or_create.assert_called_once()
