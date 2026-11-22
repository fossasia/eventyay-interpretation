"""Privacy and fail-open behavior of plugin operational logs."""

import logging
from types import SimpleNamespace

import pytest
import requests

from interpretation import operational_log
from interpretation.operational_log import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    log_operation,
    logged_request,
    mark_job_failed,
    request_context,
    traced_job,
)


@pytest.fixture
def captured(monkeypatch):
    records = []

    def fake_log(level, action, extra=None):
        records.append((level, action, extra or {}))

    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", fake_log)
    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", None)
    return records


def test_drops_email_token_and_unknown_fields(captured):
    log_operation(
        "plugin.loaded",
        OUTCOME_SUCCESS,
        backend="paypal",
        email="person@example.com",
        token="secret-token",
        body="{raw}",
        event_id=12,
    )
    assert len(captured) == 1
    extra = captured[0][2]
    assert extra["backend"] == "paypal"
    assert extra["event_id"] == 12
    assert extra["outcome"] == "success"
    assert "email" not in extra
    assert "token" not in extra
    assert "body" not in extra
    blob = str(extra)
    assert "person@example.com" not in blob
    assert "secret-token" not in blob


def test_rejects_unsafe_action_and_backend(captured):
    log_operation("has space", OUTCOME_SUCCESS, backend="paypal")
    log_operation("connection.request", OUTCOME_FAILURE, backend="pay pal", error_code="not safe")
    assert len(captured) == 1
    assert captured[0][1] == "connection.request"
    extra = captured[0][2]
    assert "backend" not in extra
    assert "error_code" not in extra
    assert "pay pal" not in str(captured)
    assert "not safe" not in str(captured)


def test_logging_failure_is_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", boom)
    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", None)
    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend="paypal")


def test_host_emitter_failure_falls_back_to_logger(monkeypatch):
    records = []

    def fake_log(level, action, extra=None):
        records.append((level, action, extra or {}))

    def broken_emitter(*args, **kwargs):
        raise RuntimeError("host emitter broken")

    monkeypatch.setattr(operational_log, "_HOST_LOG_EVENT", broken_emitter)
    monkeypatch.setattr(logging.getLogger("eventyay.plugins"), "log", fake_log)

    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend="interpretation")
    assert len(records) == 1
    assert records[0][1] == "plugin.loaded"
    assert records[0][2]["backend"] == "interpretation"


def test_object_id_cleaning_failure_is_swallowed(captured):
    class BadStr:
        def __str__(self):
            raise RuntimeError("broken __str__")

    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend="interpretation", object_id=BadStr())
    assert captured == []


def test_logged_request_records_status_without_url(captured, monkeypatch):
    class Response:
        status_code = 201

    def fake_post(url, **kwargs):
        assert url == "https://example.invalid/secret"
        assert kwargs["json"]["token"] == "raw"
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    logged_request("paypal", "POST", "https://example.invalid/secret", json={"token": "raw"})
    assert captured[-1][1] == "connection.request"
    extra = captured[-1][2]
    assert extra["status"] == 201
    assert extra["outcome"] == "success"
    assert "example.invalid" not in str(extra)
    assert "raw" not in str(extra)


def test_logged_request_honors_requests_request_patch(captured, monkeypatch):
    class Response:
        status_code = 204

    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://example.invalid/status"
        return Response()

    monkeypatch.setattr(requests, "request", fake_request)
    logged_request("interpretation", "GET", "https://example.invalid/status")
    extra = captured[-1][2]
    assert extra["status"] == 204
    assert extra["outcome"] == "success"


def test_logged_request_ok_statuses_treats_404_as_success(captured, monkeypatch):
    class Response:
        status_code = 404

    called_kwargs = {}

    def fake_delete(url, **kwargs):
        called_kwargs.update(kwargs)
        return Response()

    monkeypatch.setattr(requests, "delete", fake_delete)
    resp = logged_request(
        "interpretation",
        "DELETE",
        "https://example.invalid/webhooks/1",
        allow_redirects=False,
        ok_statuses=(404,),
    )
    assert resp.status_code == 404
    assert called_kwargs == {"allow_redirects": False}
    extra = captured[-1][2]
    assert extra["status"] == 404
    assert extra["outcome"] == "success"
    assert "error_code" not in extra

    logged_request("interpretation", "DELETE", "https://example.invalid/webhooks/1")
    extra_fail = captured[-1][2]
    assert extra_fail["status"] == 404
    assert extra_fail["outcome"] == "failure"
    assert extra_fail["error_code"] == "http_error"


def test_send_request_preserves_head_redirect_default(captured, monkeypatch):
    seen = {}

    class Response:
        status_code = 302

    def fake_session_request(self, method, url, **kwargs):
        seen["method"] = method
        seen["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(requests.sessions.Session, "request", fake_session_request)
    logged_request("interpretation", "HEAD", "https://example.invalid/head")
    assert seen["method"] == "head"
    assert seen["kwargs"]["allow_redirects"] is False


def test_traced_job_handles_celery_metadata_and_swallowed_failures(captured):
    task_self = SimpleNamespace(request=SimpleNamespace(id="task-uuid-123", retries=2))

    @traced_job("interpretation.test_job")
    def ok_task(self):
        return "done"

    assert ok_task(task_self) == "done"
    assert [c[1] for c in captured] == ["job.start", "job.finish"]
    assert captured[0][2]["job_id"] == "task-uuid-123"
    assert captured[0][2]["retry_count"] == 2
    assert captured[1][2]["outcome"] == "success"
    assert isinstance(captured[1][2]["duration_ms"], int)

    captured.clear()

    @traced_job("interpretation.test_job")
    def swallowed_failure_task(self):
        mark_job_failed("max_retries")
        return "fallback"

    assert swallowed_failure_task(task_self) == "fallback"
    assert [c[1] for c in captured] == ["job.start", "job.fail"]
    assert captured[1][2]["outcome"] == "failure"
    assert captured[1][2]["error_code"] == "max_retries"
    assert isinstance(captured[1][2]["duration_ms"], int)


def test_traced_job_handles_retry_and_exception(captured):
    class Retry(Exception):
        pass

    @traced_job("interpretation.test_job")
    def retrying_task():
        raise Retry("later")

    with pytest.raises(Retry):
        retrying_task()
    assert [c[1] for c in captured] == ["job.start", "job.retry"]
    assert captured[1][2]["error_code"] == "retry"


def test_app_ready_logs_only_after_startup_succeeds(captured, monkeypatch):
    import sys
    from types import ModuleType

    from interpretation.apps import InterpretationApp

    app = InterpretationApp("interpretation", sys.modules["interpretation"])

    broken_mod = ModuleType("interpretation.video_integration")

    def boom():
        raise RuntimeError("patch failed")

    broken_mod.install_video_integration = boom
    monkeypatch.setitem(sys.modules, "interpretation.video_integration", broken_mod)
    monkeypatch.setitem(sys.modules, "interpretation.signals", ModuleType("interpretation.signals"))

    with pytest.raises(RuntimeError, match="patch failed"):
        app.ready()
    assert len(captured) == 1
    assert captured[0][1] == "plugin.load"
    assert captured[0][2]["outcome"] == "failure"
    assert captured[0][2]["error_code"] == "startup_failed"

    captured.clear()
    broken_mod.install_video_integration = lambda: None
    app.ready()
    assert len(captured) == 1
    assert captured[0][1] == "plugin.loaded"
    assert captured[0][2]["backend"] == "interpretation"


def test_webhook_logs_failure_when_handler_raises(captured, monkeypatch):
    import hashlib
    import hmac
    import json
    import time

    from django.test import RequestFactory

    from interpretation import views_webhooks
    from interpretation.views_webhooks import VoxbentoWebhookReceiverView

    body = json.dumps(
        {
            "event_type": "booth.transcription.started",
            "event_slug": "my-event",
            "data": {"booth_id": "my-event-1-en"},
        }
    ).encode("utf-8")
    ts = int(time.time())
    secret = "whsec-secret"
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

    rf = RequestFactory()
    request = rf.post(
        "/interpretation/voxbento/webhook/",
        data=body,
        content_type="application/json",
        HTTP_X_VOXBENTO_SIGNATURE=f"t={ts},v1={sig}",
    )

    fake_grant = SimpleNamespace(event_id=77, event=SimpleNamespace(id=77), webhook_secret_key=secret)
    monkeypatch.setattr(views_webhooks.VoxbentoOAuthGrant.objects, "get", lambda **kw: fake_grant)

    view = VoxbentoWebhookReceiverView()

    def broken_handler(event, data):
        raise RuntimeError("db failure")

    monkeypatch.setattr(view, "handle_transcription_started", broken_handler)

    with pytest.raises(RuntimeError, match="db failure"):
        view.post(request)

    assert len(captured) == 1
    assert captured[0][1] == "webhook.inbound"
    extra = captured[0][2]
    assert extra["outcome"] == "failure"
    assert extra["error_code"] == "dispatch_failed"
    assert extra["status"] == 500
    assert extra["event_id"] == 77


def test_request_context_rejects_non_opaque_header_values(captured):
    for unsafe_val in ("15551234567", "sk-test-secret"):
        req = SimpleNamespace(headers={"X-Request-ID": unsafe_val}, META={"HTTP_X_REQUEST_ID": unsafe_val})
        with request_context(req) as req_id:
            assert req_id != unsafe_val
            assert len(req_id) == 32
            int(req_id, 16)
            log_operation("room.sync", OUTCOME_SUCCESS, backend="interpretation")
        assert captured[-1][2]["request_id"] == req_id
        assert unsafe_val not in str(captured[-1][2])

    valid_hex = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    req_valid = SimpleNamespace(headers={"X-Request-ID": valid_hex}, META={})
    with request_context(req_valid) as req_id:
        assert req_id == valid_hex


def test_logged_request_propagates_correlation_headers(captured, monkeypatch):
    seen_headers = {}

    class Response:
        status_code = 200

    def fake_post(url, **kwargs):
        seen_headers.update(kwargs.get("headers") or {})
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    req = SimpleNamespace(headers={"X-Request-ID": "req-0123456789abcdef"}, META={})
    caller_headers = {"Authorization": "Bearer secret-token"}

    @traced_job("interpretation.sync")
    def run_job():
        with request_context(req):
            logged_request("interpretation", "POST", "https://example.invalid/sync", headers=caller_headers)

    run_job()
    assert seen_headers["X-Request-ID"] == "req-0123456789abcdef"
    assert "X-Job-ID" in seen_headers
    assert "X-Request-ID" not in caller_headers
    assert "secret-token" not in str(captured)
