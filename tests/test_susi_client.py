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

    def close(self):
        return None


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
    result = SusiClient("https://susi.example.com", "bad-token").verify()
    assert result.ok is False
    assert "invalid" in result.message.lower() or "expired" in result.message.lower()


def test_verify_without_token(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(200, {"authenticated": False}),
    )
    result = SusiClient("https://susi.example.com").verify()
    assert result.ok is False
    assert "token" in result.message.lower()


def test_verify_server_error(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(503, {}),
    )
    result = SusiClient("https://susi.example.com", "tok").verify()
    assert result.ok is False


def test_verify_unreachable_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(SusiError):
        SusiClient("https://susi.example.com", "tok").verify()


def test_login_returns_token_from_cookie(monkeypatch):
    def fake_post(url, **kwargs):
        assert url.endswith("/auth/api/login")
        assert kwargs["json"] == {"email": "a@b.c", "password": "secret"}
        return FakeResponse(
            200, {"status": "success"}, cookies={"access_token_cookie": "jwt-xyz"}
        )

    monkeypatch.setattr(requests, "post", fake_post)
    client = SusiClient("https://susi.example.com")
    token = client.login("a@b.c", "secret")
    assert token == "jwt-xyz"
    assert client.auth_token == "jwt-xyz"


def test_login_rejects_bad_credentials(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResponse(401, {"status": "error"})
    )
    with pytest.raises(SusiError):
        SusiClient("https://susi.example.com").login("a@b.c", "wrong")


def test_create_session_returns_tenant_id(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(200, {"tenant_id": "abc123", "source": "url"}),
    )
    tenant = SusiClient("https://susi.example.com", "tok").create_session("url")
    assert tenant == "abc123"


def test_create_session_failure_raises(monkeypatch):
    monkeypatch.setattr(
        requests,
        "request",
        lambda *a, **k: FakeResponse(400, {"error": "bad source"}),
    )
    with pytest.raises(SusiError):
        SusiClient("https://susi.example.com", "tok").create_session("nope")


def test_latest_transcript_passes_params(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, {"chunk_id": "5", "transcript": "hello world"})

    monkeypatch.setattr(requests, "request", fake_request)
    result = SusiClient("https://susi.example.com", "tok").latest_transcript("abc")
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/transcripts/latest")
    assert captured["params"] == {"tenant_id": "abc", "sentences": "true"}
    assert result.data["transcript"] == "hello world"


def test_open_translate_stream_sends_params_and_token(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        captured["stream"] = kwargs.get("stream")
        captured["timeout"] = kwargs.get("timeout")
        return FakeResponse(200, content=b"data: {}\n\n")

    monkeypatch.setattr(requests, "get", fake_get)
    resp = SusiClient("https://susi.example.com", "tok").open_translate_stream(
        "abc", target_lang="de", last_chunk_id=7, read_timeout=30
    )
    assert captured["url"].endswith("/api/v1/translate/stream")
    assert captured["params"] == {
        "tenant_id": "abc",
        "last_chunk_id": 7,
        "target_lang": "de",
    }
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["stream"] is True
    assert captured["timeout"][1] == 30
    assert resp.ok


def test_open_translate_stream_raises_on_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(401, content=b""))
    with pytest.raises(SusiError):
        SusiClient("https://susi.example.com", "tok").open_translate_stream("abc")
