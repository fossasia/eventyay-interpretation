"""Tests for the interpretation overview landing page."""

import pytest

from interpretation.models import RoomInterpretation

pytestmark = pytest.mark.django_db


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def test_overview_renders(organizer_client, dashboard_url):
    response = organizer_client.get(dashboard_url)

    assert response.status_code == 200
    assert "Room settings" in response.content.decode()


def test_overview_shows_room_stats(organizer_client, dashboard_url, room):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        target_languages=["de", "fr"],
    )

    response = organizer_client.get(dashboard_url)

    assert response.status_code == 200
    content = response.content.decode()
    assert "Main Stage" in content
    assert "Live now" in content
    assert "1" in content


def test_room_settings_renders_table(organizer_client, rooms_url, room):
    response = organizer_client.get(rooms_url)

    assert response.status_code == 200
    content = response.content.decode()
    assert "Room settings" in content
    assert "Main Stage" in content
    assert "Configure" in content


def test_overview_links_to_room_settings(organizer_client, dashboard_url, rooms_url):
    response = organizer_client.get(dashboard_url)

    assert response.status_code == 200
    assert rooms_url in response.content.decode()
