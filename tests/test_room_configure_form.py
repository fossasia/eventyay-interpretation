"""Tests for RoomConfigureForm."""

from interpretation.forms import RoomConfigureForm
from interpretation.models import RoomInterpretation


class _FakeEvent:
    def get_plugins(self):
        return ["interpretation"]

    class settings:
        @staticmethod
        def get(key, default=None, as_type=str):
            return default


def test_room_configure_form_lists_interpreters():
    form = RoomConfigureForm(event=_FakeEvent())
    ids = [choice[0] for choice in form.fields["interpreter"].choices]
    assert RoomInterpretation.INTERPRETER_NONE in ids
    assert RoomInterpretation.INTERPRETER_SUSI in ids


def test_room_configure_form_accepts_interpreter_and_enabled():
    form = RoomConfigureForm(
        event=_FakeEvent(),
        data={
            "interpreter": RoomInterpretation.INTERPRETER_SUSI,
            "room_enabled": True,
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["interpreter"] == RoomInterpretation.INTERPRETER_SUSI
    assert form.cleaned_data["room_enabled"] is True
