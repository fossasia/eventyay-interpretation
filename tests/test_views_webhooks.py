"""Tests for the VoxBento webhook receiver."""

import ast
import hashlib
import hmac
import importlib
import inspect
import json
import time
from unittest.mock import patch

import pytest
from django.urls import reverse

from interpretation import views_webhooks
from interpretation.models import RoomInterpretation, VoxbentoOAuthGrant

pytestmark = pytest.mark.django_db

WEBHOOK_SECRET = "whsec-test"
NOTIFY_TARGET = "interpretation.room_control.notify_video_room_config_changed"


@pytest.fixture
def grant(event):
    return VoxbentoOAuthGrant.objects.create(event=event, webhook_secret_key=WEBHOOK_SECRET)


@pytest.fixture
def interpretation(connected_room):
    return connected_room.interpretation


def _post_webhook(client, event, room, event_type, **data):
    body = json.dumps(
        {
            "event_type": event_type,
            "event_slug": event.slug,
            "data": {"booth_id": f"{event.slug}-{room.pk}-en", **data},
        }
    )
    timestamp = int(time.time())
    signature = hmac.new(WEBHOOK_SECRET.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return client.post(
        reverse("plugins:interpretation:voxbento_webhook"),
        data=body,
        content_type="application/json",
        HTTP_X_VOXBENTO_SIGNATURE=f"t={timestamp},v1={signature}",
    )


def test_transcription_started_marks_running_and_notifies(client, event, connected_room, interpretation, grant):
    with patch(NOTIFY_TARGET) as notify:
        response = _post_webhook(client, event, connected_room, "booth.transcription.started", session_id="sess-1")

    interpretation.refresh_from_db()
    assert response.status_code == 200
    assert interpretation.status == RoomInterpretation.STATUS_RUNNING
    assert interpretation.backend_session_id == "sess-1"
    notify.assert_called_once_with(event)


def test_transcription_stopped_marks_idle_and_notifies(client, event, connected_room, interpretation, grant):
    interpretation.status = RoomInterpretation.STATUS_RUNNING
    interpretation.backend_session_id = "sess-1"
    interpretation.save(update_fields=["status", "backend_session_id"])

    with patch(NOTIFY_TARGET) as notify:
        response = _post_webhook(client, event, connected_room, "booth.transcription.stopped")

    interpretation.refresh_from_db()
    assert response.status_code == 200
    assert interpretation.status == RoomInterpretation.STATUS_IDLE
    assert interpretation.backend_session_id == ""
    notify.assert_called_once_with(event)


def test_interpreter_joined_returns_ok(client, event, connected_room, interpretation, grant):
    response = _post_webhook(
        client,
        event,
        connected_room,
        "booth.interpreter.joined",
        participant_id="p1",
        display_name="Interpreter",
        language="en",
    )

    interpretation.refresh_from_db()
    assert response.status_code == 200
    assert interpretation.backend_config["active_interpreters"][0]["participant_id"] == "p1"


def test_webhook_views_import_notify_from_a_module_that_defines_it():
    tree = ast.parse(inspect.getsource(views_webhooks))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and "notify_video_room_config_changed" in [a.name for a in node.names]
    ]

    assert imports
    for node in imports:
        module = importlib.import_module("." * node.level + (node.module or ""), package="interpretation")
        assert hasattr(module, "notify_video_room_config_changed"), module.__name__
