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
    content = response.content.decode()
    assert "Room settings" in content
    assert "Enable live interpretation for this event" in content


def test_overview_disable_stops_sessions(
    monkeypatch, organizer_client, dashboard_url, connected_event, room
):
    from interpretation.settings import SETTING_IS_ENABLED

    connected_event.settings.set(SETTING_IS_ENABLED, True)
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        status=RoomInterpretation.STATUS_RUNNING,
        backend_session_id="tenant-1",
    )
    stopped = []

    def fake_stop_all(event):
        stopped.append(event)

    monkeypatch.setattr(
        "interpretation.room_control.stop_all_event_sessions",
        fake_stop_all,
    )

    response = organizer_client.post(
        dashboard_url,
        {
            "interpretation_event_settings_save": "1",
            "interpretation-interpretation_is_enabled": "",
        },
    )

    assert response.status_code == 302
    assert stopped == [connected_event]
    connected_event.settings.flush()
    assert connected_event.settings.get(SETTING_IS_ENABLED, as_type=bool) is False


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
