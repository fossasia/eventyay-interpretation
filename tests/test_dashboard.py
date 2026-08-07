"""POST tests for event-level interpreter credentials and room settings."""

import pytest
from django.contrib.messages import get_messages
from django.test import override_settings

from interpretation.interpreter_credentials import get_susi_auth_token, is_susi_configured
from interpretation.models import RoomInterpretation
from interpretation.susi import SusiResult
from tests.conftest import SUSI_EVENT_CREDENTIALS, susi_connect_payload

pytestmark = pytest.mark.django_db


@override_settings(SITE_URL="https://testserver")
def test_interpreter_connect_stores_event_credentials(
    organizer_client, event, interpreters_url, monkeypatch,
):
    from interpretation.susi import SusiLoginResult

    def fake_login(self, email, password):
        return SusiLoginResult(
            token="jwt-test-token",
            email=email,
            name="SUSI User",
        )

    monkeypatch.setattr("interpretation.forms.SusiClient.login", fake_login)

    response = organizer_client.post(interpreters_url, susi_connect_payload())

    assert response.status_code == 302
    from eventyay.base.models import Event

    event = Event.objects.get(pk=event.pk)
    assert is_susi_configured(event)
    assert get_susi_auth_token(event) == "jwt-test-token"

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("connected" in message.lower() for message in messages)


@override_settings(SITE_URL="https://testserver")
def test_interpreter_test_calls_verify_with_event_token(
    monkeypatch, organizer_client, connected_event, interpreters_url,
):
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

    monkeypatch.setattr("interpretation.interpreter_credentials.SusiClient", FakeSusiClient)

    response = organizer_client.post(
        interpreters_url,
        {
            "interpretation_interpreter_id": "susi",
            "interpretation_interpreter_action": "test",
        },
    )

    assert response.status_code == 302
    assert calls == [("https://susi.example.com", "jwt-test-token")]

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("connection successful" in message.lower() for message in messages)


@override_settings(SITE_URL="https://testserver")
def test_interpreter_test_warns_when_verify_rejects_token(
    monkeypatch, organizer_client, connected_event, interpreters_url,
):
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

    monkeypatch.setattr("interpretation.interpreter_credentials.SusiClient", FakeSusiClient)

    response = organizer_client.post(
        interpreters_url,
        {
            "interpretation_interpreter_id": "susi",
            "interpretation_interpreter_action": "test",
        },
    )

    assert response.status_code == 302
    assert (
        get_susi_auth_token(connected_event)
        == SUSI_EVENT_CREDENTIALS["interpretation_susi_auth_token"]
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("connection issue" in message.lower() for message in messages)
    assert any("invalid" in message.lower() for message in messages)


@override_settings(SITE_URL="https://testserver")
def test_interpreter_test_without_credentials_shows_error(
    monkeypatch, organizer_client, event, interpreters_url,
):
    calls = []

    class FakeSusiClient:
        def __init__(self, *args, **kwargs):
            calls.append(True)

    monkeypatch.setattr("interpretation.interpreter_credentials.SusiClient", FakeSusiClient)

    response = organizer_client.post(
        interpreters_url,
        {
            "interpretation_interpreter_id": "susi",
            "interpretation_interpreter_action": "test",
        },
    )

    assert response.status_code == 302
    assert calls == []
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("sign in" in message.lower() for message in messages)
