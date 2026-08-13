"""Authorization tests for interpretation commons views."""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


@pytest.fixture
def restricted_client(client, db, organizer):
    from eventyay.base.models import Team

    user = User.objects.create_user(
        email="restricted@example.com",
        password="testpass123",
        fullname="Restricted",
        locale="en",
    )
    team = Team.objects.create(
        organizer=organizer,
        name="View Only",
        all_events=True,
        can_change_event_settings=False,
    )
    team.members.add(user)
    client.force_login(user)
    return client


def test_anonymous_user_redirected_from_dashboard(client, dashboard_url):
    response = client.get(dashboard_url)

    assert response.status_code == 302
    assert "login" in response.url.lower() or "account" in response.url.lower()


def test_restricted_user_denied_dashboard(restricted_client, dashboard_url):
    response = restricted_client.get(dashboard_url)

    assert response.status_code in {403, 302}


def test_restricted_user_denied_interpretation_streams_api(
    restricted_client, event, room
):
    org = event.organizer.slug
    slug = event.slug
    url = (
        f"/api/v1/organizers/{org}/events/{slug}/rooms/{room.pk}/interpretation/streams/"
    )
    response = restricted_client.get(url)

    assert response.status_code == 403


def test_restricted_user_denied_interpretation_config_api(
    restricted_client, event, room
):
    org = event.organizer.slug
    slug = event.slug
    url = (
        f"/api/v1/organizers/{org}/events/{slug}/rooms/{room.pk}/interpretation/config/"
    )
    response = restricted_client.get(url)

    assert response.status_code == 403


def test_cross_event_room_action_returns_404(
    organizer_client, event, connected_event, rooms_url
):
    from eventyay.base.models import Event, Room

    other = Event.objects.create(
        organizer=event.organizer,
        name="Other Event",
        slug="otherevent",
        date_from=event.date_from,
        date_to=event.date_to,
        currency="USD",
        locale="en",
        is_public=True,
        live=True,
        email="other@example.com",
    )
    other.plugins = "interpretation"
    other.save(update_fields=["plugins"])
    other_room = Room.objects.create(event=other, name="Other Room")

    response = organizer_client.post(
        rooms_url,
        {
            "interpretation_room_id": str(other_room.pk),
            "interpretation_room_action": "save",
            "room-999-interpreter": "susi",
        },
    )

    assert response.status_code == 404
