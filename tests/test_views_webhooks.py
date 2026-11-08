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


def _sign(body, secret=WEBHOOK_SECRET):
    timestamp = int(time.time())
    digest = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post_raw(client, body, signature):
    return client.post(
        reverse("plugins:interpretation:voxbento_webhook"),
        data=body,
        content_type="application/json",
        HTTP_X_VOXBENTO_SIGNATURE=signature,
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


def test_webhook_is_rejected_after_the_organizer_disconnects(client, event, connected_room, interpretation, grant):
    grant.is_disconnected = True
    grant.save(update_fields=["is_disconnected"])

    with patch(NOTIFY_TARGET) as notify:
        response = _post_webhook(client, event, connected_room, "booth.transcription.started", session_id="sess-1")

    interpretation.refresh_from_db()
    assert response.status_code == 401
    assert interpretation.status != RoomInterpretation.STATUS_RUNNING
    notify.assert_not_called()


def test_non_ascii_signature_is_rejected_instead_of_crashing(client, event, grant):
    body = json.dumps({"event_type": "booth.interpreter.joined", "event_slug": event.slug, "data": {}})

    response = _post_raw(client, body, f"t={int(time.time())},v1=éé")

    assert response.status_code == 401


def test_body_that_is_not_valid_utf8_is_a_bad_request(client, event, grant):
    response = _post_raw(client, b"\xc3\x28", f"t={int(time.time())},v1=abc")

    assert response.status_code == 400


def test_unknown_event_slug_looks_like_a_bad_signature(client, event, grant):
    body = json.dumps({"event_type": "booth.interpreter.joined", "event_slug": "no-such-event", "data": {}})

    response = _post_raw(client, body, _sign(body))

    assert response.status_code == 401


def test_null_data_with_a_valid_signature_is_ignored(client, event, grant):
    body = json.dumps({"event_type": "booth.transcription.started", "event_slug": event.slug, "data": None})

    response = _post_raw(client, body, _sign(body))

    assert response.status_code == 200


def test_same_event_slug_under_two_organizers_still_reaches_the_right_event(
    client, event, connected_room, interpretation, grant
):
    from eventyay.base.models import Event, Organizer

    other_organizer = Organizer.objects.create(name="Other Organizer", slug="otherorg")
    other_event = Event.objects.create(
        organizer=other_organizer,
        name="Other Event",
        slug=event.slug,
        date_from=event.date_from,
        date_to=event.date_to,
        currency="USD",
        locale="en",
        email="other@example.com",
    )
    VoxbentoOAuthGrant.objects.create(event=other_event, webhook_secret_key="whsec-other")

    with patch(NOTIFY_TARGET) as notify:
        response = _post_webhook(client, event, connected_room, "booth.transcription.started", session_id="sess-1")

    interpretation.refresh_from_db()
    assert response.status_code == 200
    assert interpretation.status == RoomInterpretation.STATUS_RUNNING
    notify.assert_called_once_with(event)


def test_same_event_slug_is_resolved_to_the_grant_whose_secret_signed_the_request(client, event, grant):
    from eventyay.base.models import Event, Organizer

    other_organizer = Organizer.objects.create(name="Other Organizer", slug="otherorg")
    other_event = Event.objects.create(
        organizer=other_organizer,
        name="Other Event",
        slug=event.slug,
        date_from=event.date_from,
        date_to=event.date_to,
        currency="USD",
        locale="en",
        email="other@example.com",
    )
    # Created after `grant`, so a selector that always picks the first grant for the slug would get this wrong.
    VoxbentoOAuthGrant.objects.create(event=other_event, webhook_secret_key="whsec-other")
    body = json.dumps({"event_type": "booth.interpreter.joined", "event_slug": event.slug, "data": {}})
    view_handler = "interpretation.views_webhooks.VoxbentoWebhookReceiverView.handle_interpreter_joined"

    with patch(view_handler) as handler:
        response = _post_raw(client, body, _sign(body, secret="whsec-other"))

    assert response.status_code == 200
    handler.assert_called_once_with(other_event, {})


def test_event_type_that_is_not_a_string_is_a_bad_request(client, event, grant):
    body = json.dumps({"event_type": ["booth.interpreter.joined"], "event_slug": event.slug, "data": {}})

    response = _post_raw(client, body, _sign(body))

    assert response.status_code == 400


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


def test_webhook_room_resync_rate_limiting(event):
    from unittest.mock import patch

    from django.core.cache import cache

    from interpretation.views_webhooks import VoxbentoWebhookReceiverView

    cache.clear()
    view = VoxbentoWebhookReceiverView()

    with patch("interpretation.tasks.sync_all_rooms_to_voxbento.delay") as mock_sync:
        # First unknown booth ID -> triggers sync task
        room1 = view._get_room_from_booth_id(event, f"{event.slug}-99999-en")
        assert room1 is None
        assert mock_sync.call_count == 1
        assert mock_sync.call_args[0] == (event.id,)

        # Second unknown booth ID within 60s cache window -> cached, does NOT trigger sync task again
        room2 = view._get_room_from_booth_id(event, f"{event.slug}-99999-en")
        assert room2 is None
        assert mock_sync.call_count == 1
