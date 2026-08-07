"""Tests for the event-level credential data migration."""

import importlib

import pytest
from django.apps import apps

from interpretation.interpreter_credentials import (
    SETTING_SUSI_AUTH_TOKEN,
    SETTING_SUSI_BASE_URL,
)
from interpretation.models import RoomInterpretation

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "interpretation.migrations.0006_move_credentials_to_rooms"
)
consolidate_interpreter_credentials_at_event = (
    _migration.consolidate_interpreter_credentials_at_event
)


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def test_migration_copies_room_credentials_to_event(event, room):
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        backend_config={
            "susi_base_url": "https://room.example.com",
            "susi_auth_token": "room-token",
            "susi_account_email": "room@example.com",
            "susi_account_name": "Room User",
        },
    )

    consolidate_interpreter_credentials_at_event(apps, None)

    from eventyay.base.models import Event

    event = Event.objects.get(pk=event.pk)
    assert event.settings.get(SETTING_SUSI_BASE_URL, as_type=str) == "https://room.example.com"
    assert event.settings.get(SETTING_SUSI_AUTH_TOKEN, as_type=str) == "room-token"
    interpretation.refresh_from_db()
    assert "susi_auth_token" not in interpretation.backend_config


def test_migration_keeps_existing_event_credentials(event, room):
    event.settings.set("interpretation_susi_base_url", "https://event.example.com")
    event.settings.set("interpretation_susi_auth_token", "event-token")

    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        backend_config={
            "susi_base_url": "https://room.example.com",
            "susi_auth_token": "room-token",
        },
    )

    consolidate_interpreter_credentials_at_event(apps, None)

    assert event.settings.get(SETTING_SUSI_AUTH_TOKEN, as_type=str) == "event-token"
    interpretation.refresh_from_db()
    assert "susi_auth_token" not in interpretation.backend_config
