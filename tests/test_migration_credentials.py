"""Tests for the per-room credential data migration."""

import importlib

import pytest
from django.apps import apps

from interpretation.models import RoomInterpretation

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "interpretation.migrations.0006_move_credentials_to_rooms"
)
copy_event_credentials_to_rooms = _migration.copy_event_credentials_to_rooms


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def test_migration_copies_legacy_event_credentials_to_room(event, room):
    event.settings.set("interpretation_base_url", "https://legacy.example.com")
    event.settings.set("interpretation_auth_token", "legacy-token")
    event.settings.set("interpretation_susi_email", "legacy@example.com")
    event.settings.set("interpretation_susi_name", "Legacy User")

    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )

    copy_event_credentials_to_rooms(apps, None)

    interpretation.refresh_from_db()
    assert interpretation.backend_config["susi_base_url"] == "https://legacy.example.com"
    assert interpretation.backend_config["susi_auth_token"] == "legacy-token"
    assert interpretation.backend_config["susi_account_email"] == "legacy@example.com"
    assert interpretation.backend_config["susi_account_name"] == "Legacy User"


def test_migration_skips_room_that_already_has_credentials(event, room):
    event.settings.set("interpretation_base_url", "https://legacy.example.com")
    event.settings.set("interpretation_auth_token", "legacy-token")

    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        backend_config={
            "susi_base_url": "https://room.example.com",
            "susi_auth_token": "room-token",
        },
    )

    copy_event_credentials_to_rooms(apps, None)

    interpretation.refresh_from_db()
    assert interpretation.backend_config["susi_auth_token"] == "room-token"
    assert interpretation.backend_config["susi_base_url"] == "https://room.example.com"
