"""Tests for event-level SUSI credential helpers."""

from interpretation.forms import SusiInterpreterCredentialsForm, verify_susi_connection
from interpretation.interpreter_credentials import SETTING_SUSI_BASE_URL
from interpretation.susi import SusiResult
from tests.conftest import SUSI_EVENT_CREDENTIALS, apply_susi_event_credentials

SUSI_CLIENT = "interpretation.backends.susi_credentials.SusiClient"


def test_test_susi_connection_uses_event_credentials(monkeypatch, event):
    calls = []
    logged = []

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

    monkeypatch.setattr(SUSI_CLIENT, FakeSusiClient)
    monkeypatch.setattr(
        "interpretation.forms.messages.success",
        lambda request, message: logged.append(message),
    )
    monkeypatch.setattr("interpretation.forms.messages.error", lambda *a, **k: None)

    apply_susi_event_credentials(event)
    verify_susi_connection(event, request=type("R", (), {})())
    assert calls == [("https://susi.example.com", "jwt-test-token")]
    assert logged


def test_susi_credentials_form_keeps_stored_base_url_when_post_empty(event):
    apply_susi_event_credentials(event)
    form = SusiInterpreterCredentialsForm(
        data={"interpretation_base_url": ""},
        event=event,
    )
    assert form.is_valid(), form.errors
    assert form.resolved_base_url() == SUSI_EVENT_CREDENTIALS[SETTING_SUSI_BASE_URL]
