"""Tests for SUSI credential helpers used by the dashboard."""

from interpretation.backend_credentials import (
    SUSI_AUTH_TOKEN,
    SUSI_BASE_URL,
)
from interpretation.forms import RoomSusiCredentialsForm, verify_susi_connection
from interpretation.susi import SusiResult


class _FakeInterpretation:
    def __init__(self, config=None):
        self.backend_config = dict(config or {})
        self.saved = False

    def save(self, update_fields=None):
        self.saved = True


def test_test_susi_connection_uses_room_credentials(monkeypatch):
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

    monkeypatch.setattr("interpretation.backend_credentials.SusiClient", FakeSusiClient)
    monkeypatch.setattr(
        "interpretation.forms.messages.success",
        lambda request, message: logged.append(message),
    )
    monkeypatch.setattr("interpretation.forms.messages.error", lambda *a, **k: None)

    interpretation = _FakeInterpretation(
        {
            SUSI_BASE_URL: "https://susi.example.com",
            SUSI_AUTH_TOKEN: "jwt-test-token",
        }
    )
    verify_susi_connection(interpretation, request=type("R", (), {})())
    assert calls == [("https://susi.example.com", "jwt-test-token")]
    assert logged


def test_room_credentials_form_keeps_stored_base_url_when_post_empty():
    interpretation = _FakeInterpretation({SUSI_BASE_URL: "https://susi.example.com"})
    form = RoomSusiCredentialsForm(
        data={"room-1-interpretation_base_url": ""},
        prefix="room-1",
        interpretation=interpretation,
    )
    assert form.is_valid(), form.errors
    assert form.resolved_base_url() == "https://susi.example.com"
