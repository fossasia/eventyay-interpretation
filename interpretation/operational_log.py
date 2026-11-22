"""Privacy-safe operational logs for this plugin.

Delegates to the host logger when it is importable. Otherwise uses
``eventyay.plugins``. Logging never raises into product code.

Emails, names, tokens, secrets, and request or response bodies are dropped.
Only opaque IDs, status codes, durations, and safe error codes are kept.
"""

from __future__ import annotations

import functools
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

try:
    from eventyay.base.operational_logging import (
        bind_job_id as _HOST_BIND_JOB_ID,
        get_job_id as _HOST_GET_JOB_ID,
        get_request_id as _HOST_GET_REQUEST_ID,
        log_event as _HOST_LOG_EVENT,
        reset_job_id as _HOST_RESET_JOB_ID,
    )
except Exception:
    _HOST_BIND_JOB_ID = None
    _HOST_GET_JOB_ID = None
    _HOST_GET_REQUEST_ID = None
    _HOST_LOG_EVENT = None
    _HOST_RESET_JOB_ID = None

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LOGGER = logging.getLogger("eventyay.plugins")
_INT_FIELDS = frozenset(
    {
        "event_id",
        "order_id",
        "user_id",
        "status",
        "duration_ms",
        "retry_count",
        "webhook_id",
        "recipient_count",
    }
)
_IDENTIFIER_FIELDS = frozenset(
    {
        "error_code",
        "backend",
        "job_name",
        "job_id",
        "request_id",
        "payment_provider",
        "model",
        "order_code",
    }
)
_LOG_ONLY_REQUEST_KWARGS = _INT_FIELDS | _IDENTIFIER_FIELDS | frozenset({"object_id"})
_REQUEST_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_REQUEST_ID: ContextVar[str | None] = ContextVar("interpretation_request_id", default=None)
_JOB_ID: ContextVar[str | None] = ContextVar("interpretation_job_id", default=None)
_JOB_FAILURE: ContextVar[dict | None] = ContextVar("interpretation_job_failure", default=None)


def get_request_id():
    value = _REQUEST_ID.get()
    if value is None and _HOST_GET_REQUEST_ID is not None:
        try:
            value = _HOST_GET_REQUEST_ID()
        except Exception:
            value = None
    return _safe_identifier(value)


def get_job_id():
    value = _JOB_ID.get()
    if value is None and _HOST_GET_JOB_ID is not None:
        try:
            value = _HOST_GET_JOB_ID()
        except Exception:
            value = None
    return _safe_identifier(value)


@contextmanager
def request_context(request=None):
    """Ensure a correlation request_id is active for the wrapped view call."""
    existing = get_request_id()
    if existing is not None:
        yield existing
        return
    candidate = None
    if request is not None:
        candidate = _safe_identifier(getattr(request, "_interpretation_request_id", None))
        if candidate is None:
            headers = getattr(request, "headers", None)
            if headers is not None and hasattr(headers, "get"):
                candidate = _safe_identifier(headers.get("X-Request-ID"))
        if candidate is None:
            meta = getattr(request, "META", None)
            if isinstance(meta, dict):
                candidate = _safe_identifier(meta.get("HTTP_X_REQUEST_ID"))
    req_id = candidate or uuid.uuid4().hex
    if request is not None:
        try:
            request._interpretation_request_id = req_id
        except Exception:
            pass
    token = _REQUEST_ID.set(req_id)
    try:
        yield req_id
    finally:
        _REQUEST_ID.reset(token)


def _safe_identifier(value):
    if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
        return value
    return None


def _clean(fields):
    extra = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key in _INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            extra[key] = value
            continue
        if key in _IDENTIFIER_FIELDS:
            cleaned = _safe_identifier(value if isinstance(value, str) else None)
            if cleaned is not None:
                extra[key] = cleaned
            continue
        if key == "object_id":
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                extra[key] = value
            else:
                cleaned = _safe_identifier(str(value))
                if cleaned is not None:
                    extra[key] = cleaned
    return extra


def log_operation(action, outcome, **fields):
    """Emit one allowlisted operational line. Failures are swallowed."""
    try:
        if not isinstance(action, str) or _safe_identifier(action) is None:
            return
        if outcome not in (OUTCOME_SUCCESS, OUTCOME_FAILURE):
            outcome = OUTCOME_FAILURE
        level = logging.WARNING if outcome == OUTCOME_FAILURE else logging.INFO
        payload = _clean(fields)
        if "request_id" not in payload:
            req_id = get_request_id()
            if req_id is not None:
                payload["request_id"] = req_id
        if "job_id" not in payload:
            job_id = get_job_id()
            if job_id is not None:
                payload["job_id"] = job_id
        if _HOST_LOG_EVENT is not None:
            try:
                _HOST_LOG_EVENT("plugins", action, outcome, level=level, **payload)
                return
            except Exception:
                pass
        extra = {
            "component": "plugins",
            "outcome": outcome,
            "action": action,
            **payload,
        }
        _LOGGER.log(level, action, extra=extra)
    except Exception:
        return


def log_plugin_loaded(backend):
    log_operation("plugin.loaded", OUTCOME_SUCCESS, backend=backend)


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _send_request(method, url, **kwargs):
    """Call requests without changing mocks or redirect defaults.

    A patched ``requests.get`` or ``requests.post`` wins. Otherwise a patched
    ``requests.request`` wins. Unpatched calls use the verb helper.
    """
    import requests
    import requests.api as requests_api

    verb_name = str(method).lower()
    if verb_name in _REQUEST_VERBS:
        patched = getattr(requests, verb_name, None)
        real = getattr(requests_api, verb_name, None)
        if patched is not None and patched is not real:
            return patched(url, **kwargs)  # codeql[py/full-ssrf]
    if requests.request is not requests_api.request:
        return requests.request(method, url, **kwargs)  # codeql[py/full-ssrf]
    if verb_name in _REQUEST_VERBS:
        real = getattr(requests_api, verb_name)
        return real(url, **kwargs)  # codeql[py/full-ssrf]
    return requests_api.request(method, url, **kwargs)  # codeql[py/full-ssrf]


def logged_request(backend, method, url, **kwargs):
    """Log status and duration for one HTTP call.

    ``ok_statuses`` is not sent to the server. Use it when the caller already
    treats a 4xx response as success. The same exception is re-raised.
    The URL and body are not logged.
    """
    import requests

    ok_statuses = kwargs.pop("ok_statuses", ())
    log_fields = {key: kwargs.pop(key) for key in tuple(kwargs) if key in _LOG_ONLY_REQUEST_KWARGS}
    log_fields.pop("backend", None)
    log_fields.pop("status", None)
    log_fields.pop("duration_ms", None)
    log_fields.pop("error_code", None)
    started = time.monotonic()
    try:
        response = _send_request(method, url, **kwargs)
    except requests.Timeout:
        log_operation(
            "connection.request",
            OUTCOME_FAILURE,
            backend=backend,
            error_code="timeout",
            duration_ms=_elapsed_ms(started),
            **log_fields,
        )
        raise
    except requests.RequestException:
        log_operation(
            "connection.request",
            OUTCOME_FAILURE,
            backend=backend,
            error_code="request_error",
            duration_ms=_elapsed_ms(started),
            **log_fields,
        )
        raise
    status = getattr(response, "status_code", None)
    status_ok = isinstance(status, int) and not isinstance(status, bool)
    tolerated = status_ok and status in set(ok_statuses)
    failed = status_ok and status >= 400 and not tolerated
    log_operation(
        "connection.request",
        OUTCOME_FAILURE if failed else OUTCOME_SUCCESS,
        backend=backend,
        status=status if status_ok else None,
        duration_ms=_elapsed_ms(started),
        error_code="http_error" if failed else None,
        **log_fields,
    )
    return response


def mark_job_failed(error_code="failed"):
    """Mark the active traced job as failed when an error is handled internally."""
    state = _JOB_FAILURE.get()
    if state is not None:
        state["failed"] = True
        state["error_code"] = error_code or "failed"


def _celery_task_fields(args):
    if not args:
        return {}
    req = getattr(args[0], "request", None)
    if req is None or not hasattr(req, "retries"):
        return {}
    fields = {}
    job_id = _safe_identifier(getattr(req, "id", None))
    if job_id is not None:
        fields["job_id"] = job_id
    retries = getattr(req, "retries", None)
    if isinstance(retries, int) and not isinstance(retries, bool):
        fields["retry_count"] = retries
    return fields


def traced_job(job_name):
    """Log job boundaries without changing the return value or exception."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            started = time.monotonic()
            task_fields = _celery_task_fields(args)
            job_id = task_fields.get("job_id") or get_job_id() or uuid.uuid4().hex
            task_fields["job_id"] = job_id
            job_token = _JOB_ID.set(job_id)
            prev_host_job_id = None
            host_bound = False
            if _HOST_BIND_JOB_ID is not None and _HOST_GET_JOB_ID is not None:
                try:
                    prev_host_job_id = _HOST_GET_JOB_ID()
                    _HOST_BIND_JOB_ID(job_id)
                    host_bound = True
                except Exception:
                    host_bound = False
            log_operation("job.start", OUTCOME_SUCCESS, job_name=job_name, **task_fields)
            state = {"failed": False, "error_code": None}
            token = _JOB_FAILURE.set(state)
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                retried = type(exc).__name__ == "Retry"
                log_operation(
                    "job.retry" if retried else "job.fail",
                    OUTCOME_FAILURE,
                    job_name=job_name,
                    error_code="retry" if retried else type(exc).__name__,
                    duration_ms=_elapsed_ms(started),
                    **task_fields,
                )
                raise
            else:
                if state["failed"]:
                    log_operation(
                        "job.fail",
                        OUTCOME_FAILURE,
                        job_name=job_name,
                        error_code=state["error_code"] or "failed",
                        duration_ms=_elapsed_ms(started),
                        **task_fields,
                    )
                else:
                    log_operation(
                        "job.finish",
                        OUTCOME_SUCCESS,
                        job_name=job_name,
                        duration_ms=_elapsed_ms(started),
                        **task_fields,
                    )
                return result
            finally:
                _JOB_FAILURE.reset(token)
                _JOB_ID.reset(job_token)
                if host_bound:
                    try:
                        if prev_host_job_id is not None:
                            _HOST_BIND_JOB_ID(prev_host_job_id)
                        elif _HOST_RESET_JOB_ID is not None:
                            _HOST_RESET_JOB_ID()
                    except Exception:
                        pass

        return wrapper

    return decorator
