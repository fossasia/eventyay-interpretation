"""Tests for RoomConfigureForm."""

import pytest

from interpretation.forms import RoomConfigureForm
from interpretation.models import RoomInterpretation


class _FakeEvent:
    id = 1
    pk = 1

    def __int__(self):
        return self.id

    def get_plugins(self):
        return ["interpretation"]

    class settings:
        @staticmethod
        def get(key, default=None, as_type=str):
            return default


@pytest.mark.django_db
def test_room_configure_form_lists_interpreters():
    form = RoomConfigureForm(event=_FakeEvent())
    ids = [choice[0] for choice in form.fields["interpreter"].choices]
    assert RoomInterpretation.INTERPRETER_NONE in ids
    assert RoomInterpretation.INTERPRETER_SUSI not in ids


@pytest.mark.django_db
def test_room_configure_form_accepts_interpreter_and_enabled():
    form = RoomConfigureForm(
        event=_FakeEvent(),
        data={
            "interpreter": RoomInterpretation.INTERPRETER_VOXBENTO,
            "room_enabled": True,
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["interpreter"] == RoomInterpretation.INTERPRETER_VOXBENTO
    assert form.cleaned_data["room_enabled"] is True
