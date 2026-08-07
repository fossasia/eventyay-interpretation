"""Tests for dashboard overview stats aggregation."""

import pytest

from interpretation.dashboard_stats import build_overview_context
from interpretation.models import RoomInterpretation

pytestmark = pytest.mark.django_db


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Workshop")


def test_build_overview_context_counts(event, room, connected_room):
    context = build_overview_context(event)

    assert context["stats"]["room_total"] == 1
    assert context["stats"]["room_enabled"] == 1
    assert context["stats"]["room_running"] == 0
    assert context["stats"]["room_needs_setup"] == 0
    assert len(context["interpreter_usage"]) == 1
    assert context["interpreter_usage"][0]["id"] == RoomInterpretation.INTERPRETER_SUSI


def test_build_overview_context_flags_unconfigured_susi(event, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    context = build_overview_context(event)

    assert context["stats"]["room_needs_setup"] == 1
    assert context["backends"][0]["configured"] is False


def test_build_overview_context_marks_event_susi_connected(
    event,
    room,
    connected_event,
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    context = build_overview_context(connected_event)

    assert context["stats"]["room_needs_setup"] == 0
    assert context["backends"][0]["configured"] is True
    assert context["backends"][0]["rooms_using"] == 1
