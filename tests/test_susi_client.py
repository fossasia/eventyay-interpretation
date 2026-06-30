"""Unit tests for the SUSI Translator API client (mocked HTTP)."""

import pytest
import requests

from interpretation.susi import SusiClient, SusiError


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, cookies=None, content=b"{}"):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.cookies = cookies or {}
        self.content = content

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._json


def test_base_url_requires_value():
    with pytest.raises(ValueError):
        SusiClient("")


def test_url_joining_handles_trailing_slash():
    client = SusiClient("https://susi.example.com/")
    assert client._url("/auth/api/status") == "https://susi.example.com/auth/api/status"
    client2 = SusiClient("https://susi.example.com")
    assert client2._url("auth/api/status") == "https://susi.example.com/auth/api/status"


def test_headers_include_bearer_token_when_present():
    assert "Authorization" not in SusiClient("https://x")._headers()
    headers = SusiClient("https://x", "tok123")._headers()
    assert headers["Authorization"] == "Bearer tok123"


def test_verify_success(monkeypatch):
    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/auth/api/status")
        assert kwargs["headers"]["Authorization"] == "Bearer good-token"
        return FakeResponse(200, {"authenticated": True, "email": "a@b.c"})

    monkeypatch.setattr(requests, "request", fake_request)
    result = SusiClient("https://susi.example.com", "good-token").verify()
    assert result.ok is True
    assert result.status_code == 200


def test_verify_invalid_token(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(200, {"authenticated": False}),
    )
    result = SusiClient("https://example.com", "bad-token").verify()
    assert result.ok is False
    assert "invalid" in result.message.lower() or "expired" in result.message.lower()


def test_verify_without_token(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(200, {"authenticated": False}),
    )
    result = SusiClient("https://example.com").verify()
    assert result.ok is False
    assert "token" in result.message.lower()


def test_verify_server_error(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(503, {}),
    )
    result = SusiClient("https://example.com", "tok").verify()
    assert result.ok is False


def test_verify_unreachable_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(SusiError):
        SusiClient("https://example.com", "tok").verify()


def test_login_returns_token_from_cookie(monkeypatch):
    def fake_post(url, **kwargs):
        assert url.endswith("/auth/api/login")
        assert kwargs["json"] == {"email": "a@b.c", "password": "secret"}
        return FakeResponse(
            200,
            {"status": "success", "email": "a@b.c", "name": "Jane"},
            cookies={"access_token_cookie": "jwt-xyz"},
        )

    monkeypatch.setattr(requests, "post", fake_post)
    client = SusiClient("https://example.com")
    result = client.login("a@b.c", "secret")
    assert result.token == "jwt-xyz"
    assert result.email == "a@b.c"
    assert client.auth_token == "jwt-xyz"


def test_login_rejects_bad_credentials(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResponse(401, {"status": "error"})
    )
    with pytest.raises(SusiError):
        SusiClient("https://example.com").login("a@b.c", "wrong")


def test_create_session_returns_tenant_id(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(200, {"tenant_id": "abc123", "source": "url"}),
    )
    tenant = SusiClient("https://example.com", "tok").create_session("url")
    assert tenant == "abc123"


def test_create_session_failure_raises(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(400, {"error": "bad source"}),
    )
    with pytest.raises(SusiError):
        SusiClient("https://example.com", "tok").create_session("nope")
