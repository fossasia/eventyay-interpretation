from interpretation.models import RoomInterpretation
from interpretation.room_control import attendee_interpretation_payload


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
