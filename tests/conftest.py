import os
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

# eventyay.config.settings requires this before Django is configured.
os.environ.setdefault("EVY_RUNNING_ENVIRONMENT", "testing")

User = get_user_model()

SUSI_EVENT_CREDENTIALS = {
    "interpretation_susi_base_url": "https://susi.example.com",
    "interpretation_susi_auth_token": "jwt-test-token",
    "interpretation_susi_account_email": "susi@example.com",
    "interpretation_susi_account_name": "SUSI User",
}


@pytest.fixture(autouse=True)
def align_site_url_with_test_client(settings):
    # ponytail: test client host is testserver; middleware redirects otherwise.
    settings.SITE_URL = "https://testserver"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="testuser@example.com",
        password="testpass123",
        fullname="Test User",
        locale="en",
    )


@pytest.fixture
def organizer(db):
    from eventyay.base.models import Organizer

    return Organizer.objects.create(
        name="Test Organizer",
        slug="testorg",
    )


@pytest.fixture
def event(db, organizer):
    from eventyay.base.models import Event

    now = timezone.now()
    event = Event.objects.create(
        organizer=organizer,
        name="Test Event",
        slug="testevent",
        date_from=now + timedelta(days=30),
        date_to=now + timedelta(days=32),
        currency="USD",
        locale="en",
        is_public=True,
        live=True,
        email="test@example.com",
    )
    event.plugins = "interpretation"
    event.save(update_fields=["plugins"])
    return event


@pytest.fixture
def team(db, organizer, user):
    from eventyay.base.models import Team

    team = Team.objects.create(
        organizer=organizer,
        name="Test Team",
        all_events=True,
        can_change_event_settings=True,
    )
    team.members.add(user)
    return team


@pytest.fixture
def organizer_client(client, user, team):
    client.force_login(user)
    return client


@pytest.fixture
def dashboard_url(event):
    from django.urls import reverse

    return reverse(
        "plugins:interpretation:dashboard",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


@pytest.fixture
def rooms_url(event):
    from django.urls import reverse

    return reverse(
        "plugins:interpretation:rooms",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


@pytest.fixture
def interpreters_url(event):
    from django.urls import reverse

    return reverse(
        "plugins:interpretation:interpreters",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


@pytest.fixture
def room(event):
    from eventyay.base.models import Room

    return Room.objects.create(event=event, name="Main Stage")


def apply_susi_event_credentials(event):
    for key, value in SUSI_EVENT_CREDENTIALS.items():
        event.settings.set(key, value)


@pytest.fixture
def connected_event(event):
    apply_susi_event_credentials(event)
    return event


@pytest.fixture
def connected_room(room, connected_event):
    from interpretation.models import RoomInterpretation

    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
    )
    return room


def susi_connect_payload(**extra):
    return {
        "interpretation_interpreter_id": "susi",
        "interpretation_interpreter_action": "connect",
        "interpretation_base_url": "https://susi.example.com",
        "susi_connect_email": "susi@example.com",
        "susi_connect_password": "secret",
        "interpretation_connect": "1",
        **extra,
    }
