from unittest.mock import Mock

import pytest
import requests
from django.db import transaction
from eventyay.base.models import Room
from eventyay.base.services.room import soft_delete_room

from interpretation.backends.voxbento_api import delete_voxbento_room
from interpretation.models import RoomInterpretation, VoxbentoOAuthGrant
from interpretation.settings import SETTING_IS_ENABLED
from interpretation.tasks import (
    _do_sync_single_room_to_voxbento,
    sync_all_rooms_to_voxbento,
    sync_single_room_to_voxbento,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def room_sync(room, monkeypatch):
    VoxbentoOAuthGrant.objects.create(event=room.event, event_provisioned=True)
    upsert = Mock(return_value={})
    delete = Mock(return_value=None)
    monkeypatch.setattr("interpretation.tasks.sync_voxbento_room", upsert)
    monkeypatch.setattr("interpretation.tasks.delete_voxbento_room", delete)
    monkeypatch.setattr("eventyay.base.models.cache.VersionedModel._set_cache_version_sync", Mock())
    monkeypatch.setattr("eventyay.base.models.cache.VersionedModel._set_cache_deleted_sync", Mock())
    return upsert, delete


@pytest.mark.parametrize("enabled", [False, True])
def test_soft_delete_syncs_only_after_commit(room, room_sync, django_capture_on_commit_callbacks, enabled):
    upsert, delete = room_sync
    room.event.settings.set(SETTING_IS_ENABLED, enabled)

    with django_capture_on_commit_callbacks(execute=True):
        deleted_room = soft_delete_room(room.event, room)
        assert deleted_room.deleted
        upsert.assert_not_called()
        delete.assert_not_called()

    room.refresh_from_db()
    assert room.deleted
    delete.assert_called_once_with(room.event, room.pk)
    upsert.assert_not_called()


def test_rolled_back_soft_delete_does_not_sync(room, room_sync, django_capture_on_commit_callbacks):
    upsert, delete = room_sync

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(ValueError), transaction.atomic():
            soft_delete_room(room.event, room)
            raise ValueError("Roll back deletion")

    room.refresh_from_db()
    assert not room.deleted
    upsert.assert_not_called()
    delete.assert_not_called()


@pytest.mark.parametrize("with_instance", [False, True])
def test_deleted_room_is_not_upserted(room, room_sync, with_instance):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)
    room.refresh_from_db()

    needs_retry = _do_sync_single_room_to_voxbento(
        room.pk, room.event_id, "upsert", room_instance=room if with_instance else None
    )

    assert not needs_retry
    upsert.assert_not_called()


def test_late_queued_upsert_does_not_recreate_deleted_room(room, room_sync):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)

    sync_single_room_to_voxbento.run(room.pk, room.event_id, "upsert")

    upsert.assert_not_called()


def test_room_update_still_upserts(room, room_sync, django_capture_on_commit_callbacks):
    upsert, delete = room_sync
    room.name = "Renamed stage"

    with django_capture_on_commit_callbacks(execute=True):
        room.save(update_fields=["name"])

    upsert.assert_called_once()
    assert upsert.call_args.args[2]["name"] == "Renamed stage"
    delete.assert_not_called()


def test_new_room_still_syncs_after_commit(event, room_sync, django_capture_on_commit_callbacks):
    upsert, delete = room_sync

    with django_capture_on_commit_callbacks(execute=True):
        room = Room.objects.create(event=event, name="Second stage")
        upsert.assert_not_called()

    upsert.assert_called_once()
    assert upsert.call_args.args[1] == room.pk
    delete.assert_not_called()


def test_hard_delete_still_syncs_after_commit(room, room_sync, django_capture_on_commit_callbacks):
    upsert, delete = room_sync
    room_id = room.pk
    event = room.event

    with django_capture_on_commit_callbacks(execute=True):
        room.delete()
        delete.assert_not_called()

    delete.assert_called_once_with(event, room_id)
    upsert.assert_not_called()


def test_delete_failure_requests_retry(room, room_sync, monkeypatch):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)
    delete.side_effect = requests.ConnectionError("VoxBento unavailable")
    retry = Mock()
    monkeypatch.setattr(sync_single_room_to_voxbento, "retry", retry)

    sync_single_room_to_voxbento.run(room.pk, room.event_id, "delete")

    retry.assert_called_once()
    upsert.assert_not_called()


def test_active_booth_delete_is_not_forced(room, room_sync):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)
    response = requests.Response()
    response.status_code = 409
    delete.side_effect = requests.HTTPError("Active booth", response=response)

    assert _do_sync_single_room_to_voxbento(room.pk, room.event_id, "delete")

    delete.assert_called_once_with(room.event, room.pk)
    upsert.assert_not_called()


@pytest.mark.parametrize("via_save", [False, True])
def test_stale_room_instance_does_not_upsert_deleted_room(room, room_sync, via_save):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)

    if via_save:
        room.name = "Stale name edit"
        room.save(update_fields=["name"])
    else:
        assert not _do_sync_single_room_to_voxbento(room.pk, room.event_id, "upsert", room_instance=room)

    assert Room.objects.get(pk=room.pk).deleted
    upsert.assert_not_called()
    delete.assert_not_called()


@pytest.mark.parametrize("hard_delete", [False, True])
@pytest.mark.parametrize("timed_out", [False, True])
def test_inflight_upsert_reconciles_concurrent_deletion(
    room, room_sync, django_capture_on_commit_callbacks, hard_delete, timed_out
):
    upsert, delete = room_sync
    room_id, event_id = room.pk, room.event_id
    remote_rooms = {room_id}

    def delete_remote_room(event, pk):
        remote_rooms.discard(pk)

    delete.side_effect = delete_remote_room

    def finish_upsert_after_delete(event, pk, payload):
        if hard_delete:
            Room.objects.filter(pk=pk)._raw_delete("default")
        else:
            Room.objects.filter(pk=pk).update(deleted=True)
        sync_single_room_to_voxbento.run(pk, event_id, "delete")
        assert not remote_rooms
        remote_rooms.add(pk)
        if timed_out:
            raise requests.Timeout("Response lost after the room was upserted")
        return {}

    upsert.side_effect = finish_upsert_after_delete

    with django_capture_on_commit_callbacks(execute=True):
        assert not _do_sync_single_room_to_voxbento(room_id, event_id, "upsert")

    assert not remote_rooms


@pytest.mark.parametrize("via_serializer", [False, True])
def test_restore_before_commit_does_not_delete_active_room(
    room, room_sync, django_capture_on_commit_callbacks, via_serializer
):
    upsert, delete = room_sync

    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        room = soft_delete_room(room.event, room)
        if via_serializer:
            from eventyay.api.serializers.rooms import RoomSerializer

            serializer = RoomSerializer(room, data={"deleted": False}, partial=True)
            assert serializer.is_valid(), serializer.errors
            serializer.save()
        else:
            room.deleted = False
            room.save(update_fields=["deleted"])
        upsert.assert_not_called()
        delete.assert_not_called()

    room.refresh_from_db()
    assert not room.deleted
    delete.assert_not_called()
    upsert.assert_called_once()


@pytest.mark.parametrize("timed_out", [False, True])
def test_inflight_delete_reconciles_concurrent_restoration(
    room, room_sync, django_capture_on_commit_callbacks, timed_out
):
    upsert, delete = room_sync
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_VOXBENTO,
        backend_session_id="77",
        backend_config={"voxbento_room_id": "77"},
    )
    Room.objects.filter(pk=room.pk).update(deleted=True)
    remote_rooms = {room.pk}

    def upsert_remote_room(event, pk, payload):
        remote_rooms.add(pk)
        return {"room_id": 77}

    upsert.side_effect = upsert_remote_room

    def finish_delete_after_restore(event, pk):
        Room.objects.filter(pk=pk).update(deleted=False)
        assert not _do_sync_single_room_to_voxbento(pk, event.pk, "upsert")
        assert remote_rooms == {pk}
        remote_rooms.discard(pk)
        if timed_out:
            raise requests.Timeout("Response lost after the room was deleted")

    delete.side_effect = finish_delete_after_restore

    with django_capture_on_commit_callbacks(execute=True):
        assert not _do_sync_single_room_to_voxbento(room.pk, room.event_id, "delete")
        assert not remote_rooms

    room.refresh_from_db()
    interpretation.refresh_from_db()
    assert not room.deleted
    assert remote_rooms == {room.pk}
    assert interpretation.backend_session_id == "77"
    assert interpretation.backend_config["voxbento_room_id"] == "77"


def test_stale_delete_task_does_not_remove_active_room(room, room_sync):
    upsert, delete = room_sync

    sync_single_room_to_voxbento.run(room.pk, room.event_id, "delete")

    delete.assert_not_called()
    upsert.assert_not_called()


@pytest.mark.parametrize("flag", ["is_disconnected", "needs_reauth", "event_provisioning_failed"])
def test_reconnection_reconciles_soft_deleted_rooms(room, room_sync, flag):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)
    VoxbentoOAuthGrant.objects.filter(event=room.event).update(**{flag: True})

    assert not _do_sync_single_room_to_voxbento(room.pk, room.event_id, "delete")
    delete.assert_not_called()

    VoxbentoOAuthGrant.objects.filter(event=room.event).update(**{flag: False})
    sync_all_rooms_to_voxbento.run(room.event_id)

    delete.assert_called_once_with(room.event, room.pk)
    upsert.assert_not_called()


@pytest.mark.parametrize("missing", ["url", "token"])
def test_delete_without_configuration_requests_retry(room, room_sync, monkeypatch, missing):
    upsert, delete = room_sync
    Room.objects.filter(pk=room.pk).update(deleted=True)
    monkeypatch.setattr("interpretation.tasks.delete_voxbento_room", delete_voxbento_room)
    monkeypatch.setattr(
        "interpretation.backends.voxbento_api.get_voxbento_base_url",
        lambda event: "" if missing == "url" else "https://voxbento.example.com",
    )
    monkeypatch.setattr("interpretation.backends.voxbento_api.get_valid_access_token", lambda pk: None)
    request = Mock()
    monkeypatch.setattr("interpretation.backends.voxbento_api.requests.delete", request)
    retry = Mock()
    monkeypatch.setattr(sync_single_room_to_voxbento, "retry", retry)

    sync_single_room_to_voxbento.run(room.pk, room.event_id, "delete")

    retry.assert_called_once()
    request.assert_not_called()
    upsert.assert_not_called()


@pytest.mark.parametrize("status", [204, 404, 409])
def test_delete_clears_remote_mapping_only_on_success(room, room_sync, monkeypatch, status):
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter=RoomInterpretation.INTERPRETER_VOXBENTO,
        backend_session_id="77",
        backend_config={"voxbento_room_id": "77", "transcription_session_id": "session-1", "other_setting": True},
        status=RoomInterpretation.STATUS_RUNNING,
    )
    Room.objects.filter(pk=room.pk).update(deleted=True)
    monkeypatch.setattr("interpretation.tasks.delete_voxbento_room", delete_voxbento_room)
    monkeypatch.setattr(
        "interpretation.backends.voxbento_api.get_voxbento_base_url", lambda event: "https://voxbento.example.com"
    )
    monkeypatch.setattr("interpretation.backends.voxbento_api.get_valid_access_token", lambda pk: "test-token")
    response = requests.Response()
    response.status_code = status
    monkeypatch.setattr("interpretation.backends.voxbento_api.requests.delete", Mock(return_value=response))

    needs_retry = _do_sync_single_room_to_voxbento(room.pk, room.event_id, "delete")

    interpretation.refresh_from_db()
    assert needs_retry == (status == 409)
    if status == 409:
        assert interpretation.backend_session_id == "77"
        assert interpretation.backend_config["voxbento_room_id"] == "77"
        assert interpretation.status == RoomInterpretation.STATUS_RUNNING
    else:
        assert interpretation.backend_session_id == ""
        assert interpretation.backend_config == {"other_setting": True}
        assert interpretation.status == RoomInterpretation.STATUS_IDLE
