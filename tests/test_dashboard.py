"""POST tests for per-room interpretation credentials."""

import pytest
from django.contrib.messages import get_messages
from django.test import override_settings

from interpretation.backend_credentials import get_susi_auth_token, is_susi_configured
from interpretation.forms import TEST_POST_KEY
from interpretation.models import RoomInterpretation
from interpretation.susi import SusiResult
from tests.conftest import SUSI_BACKEND_CONFIG, room_connect_payload

pytestmark = pytest.mark.django_db


@override_settings(SITE_URL="https://testserver")
def test_room_connect_stores_credentials(
    organizer_client, room, rooms_url, monkeypatch,
):
    from interpretation.susi import SusiLoginResult

    def fake_login(self, email, password):
        return SusiLoginResult(
            token="jwt-test-token",
            email=email,
            name="SUSI User",
        )

    monkeypatch.setattr("interpretation.forms.SusiClient.login", fake_login)

    response = organizer_client.post(rooms_url, room_connect_payload(room))

    assert response.status_code == 302
    interpretation = RoomInterpretation.objects.get(room=room)
    assert is_susi_configured(interpretation)
    assert get_susi_auth_token(interpretation) == "jwt-test-token"

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("connected" in message.lower() for message in messages)


@override_settings(SITE_URL="https://testserver")
def test_room_test_calls_verify_with_room_token(
    monkeypatch, organizer_client, connected_room, rooms_url
):
    room = connected_room
    calls = []

    class FakeSusiClient:
        def __init__(self, base_url, auth_token="", timeout=10):
            calls.append((base_url, auth_token))

        def verify(self):
            return SusiResult(
                ok=True,
                status_code=200,
                data={"authenticated": True},
                message="Connected and authenticated.",
            )

    monkeypatch.setattr("interpretation.backend_credentials.SusiClient", FakeSusiClient)

    prefix = f"room-{room.pk}"
    payload = {
        "interpretation_room_id": str(room.pk),
        "interpretation_room_action": "test",
        f"{prefix}-interpreter": "susi",
        TEST_POST_KEY: "1",
    }
    response = organizer_client.post(rooms_url, payload)

    assert response.status_code == 302
    assert calls == [("https://susi.example.com", "jwt-test-token")]

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("connection successful" in message.lower() for message in messages)


@override_settings(SITE_URL="https://testserver")
def test_room_test_warns_when_verify_rejects_token(
    monkeypatch, organizer_client, connected_room, rooms_url
):
    room = connected_room

    class FakeSusiClient:
        def __init__(self, base_url, auth_token="", timeout=10):
            self.base_url = base_url
            self.auth_token = auth_token

        def verify(self):
            return SusiResult(
                ok=False,
                status_code=200,
                data={"authenticated": False},
                message="Server reachable but token is invalid or expired.",
            )

    monkeypatch.setattr("interpretation.backend_credentials.SusiClient", FakeSusiClient)

    prefix = f"room-{room.pk}"
    payload = {
        "interpretation_room_id": str(room.pk),
        "interpretation_room_action": "test",
        f"{prefix}-interpreter": "susi",
        TEST_POST_KEY: "1",
    }
    response = organizer_client.post(rooms_url, payload)

    assert response.status_code == 302
    interpretation = RoomInterpretation.objects.get(room=room)
    assert get_susi_auth_token(interpretation) == SUSI_BACKEND_CONFIG["susi_auth_token"]

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("connection issue" in message.lower() for message in messages)
    assert any("invalid" in message.lower() for message in messages)


@override_settings(SITE_URL="https://testserver")
def test_room_test_without_credentials_shows_error(
    monkeypatch, organizer_client, room, rooms_url
):
    RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_SUSI,
        room_enabled=True,
        backend_config={},
    )
    calls = []

    class FakeSusiClient:
        def __init__(self, *args, **kwargs):
            calls.append(True)

    monkeypatch.setattr("interpretation.backend_credentials.SusiClient", FakeSusiClient)

    prefix = f"room-{room.pk}"
    payload = {
        "interpretation_room_id": str(room.pk),
        "interpretation_room_action": "test",
        f"{prefix}-interpreter": "susi",
        TEST_POST_KEY: "1",
    }
    response = organizer_client.post(rooms_url, payload)

    assert response.status_code == 302
    assert calls == []
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("sign in" in message.lower() for message in messages)
