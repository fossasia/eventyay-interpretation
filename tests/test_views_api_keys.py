import pytest
import responses

from interpretation.models import RoomInterpretation


@pytest.fixture
def voxbento_grant(connected_event):
    from interpretation.models import VoxbentoOAuthGrant

    return VoxbentoOAuthGrant.objects.create(
        event=connected_event,
        access_token="test",
        refresh_token="test",
        translation_openai_api_key="sk-valid-stored-key",
    )


@responses.activate
def test_invalid_replacement_leaves_stored_key_unchanged(
    organizer_client, connected_event, room, rooms_url, voxbento_grant
):
    """
    Test that submitting an invalid key during room configuration
    rejects the save, leaves the stored key unchanged, and marks the specific key as invalid in the UI.
    """
    # Mock validation failure for the new key
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        json={"error": "invalid_api_key"},
        status=401,
    )

    post_data = {
        f"room-{room.pk}-interpreter": "voxbento",
        f"room-{room.pk}-room_enabled": "on",
        f"room-{room.pk}-enable_transcription": "on",
        f"room-{room.pk}-transcription_provider": "local",
        f"room-{room.pk}-transcription_model": "tiny",
        f"room-{room.pk}-enable_translation": "on",
        f"room-{room.pk}-translation_provider": "openai",
        f"room-{room.pk}-translation_model": "gpt-4o",
        f"room-{room.pk}-translation_openai_api_key": "sk-invalid-new-key",
        "interpretation_room_action": "save",
        "interpretation_room_id": str(room.pk),
    }

    response = organizer_client.post(rooms_url, post_data)

    # Save should be rejected, redirect does not happen
    assert response.status_code == 200

    # 1. Stored key remains unchanged
    voxbento_grant.refresh_from_db()
    assert voxbento_grant.translation_openai_api_key == "sk-valid-stored-key"

    # 2. UI marks only the replacement as invalid
    assert b"data-invalid-keys" in response.content or b"invalid, expired, or revoked" in response.content
    assert response.context["rooms"][0]["configure_form"].invalid_api_keys.get("translation_openai") is True


@responses.activate
def test_disabling_feature_with_invalid_stored_key(organizer_client, connected_event, room, rooms_url, voxbento_grant):
    """
    Test that an organizer can save a room (e.g. disable translation) even if a stored key
    is invalid, as long as they aren't submitting a new invalid key.
    """
    from interpretation.backends.voxbento_credentials import get_voxbento_base_url

    base_url = get_voxbento_base_url(connected_event)
    # Mock token validation for sync
    responses.add(
        responses.POST,
        f"{base_url}/oauth/token",
        json={"access_token": "new", "refresh_token": "new", "expires_in": 3600},
        status=200,
    )
    # Mock voxbento api sync success for API keys
    responses.add(responses.PATCH, f"{base_url}/api/v1/events/{connected_event.slug}/api_keys", status=200)
    # Mock room sync success
    responses.add(
        responses.PUT,
        f"{base_url}/api/v1/events/{connected_event.slug}/rooms/{room.pk}",
        json={"room_id": str(room.pk)},
        status=200,
    )

    voxbento_grant.translation_openai_api_key = "sk-invalid-stored-key"
    voxbento_grant.save()

    post_data = {
        f"room-{room.pk}-interpreter": "voxbento",
        f"room-{room.pk}-room_enabled": "on",
        f"room-{room.pk}-enable_transcription": "on",
        f"room-{room.pk}-transcription_provider": "local",
        f"room-{room.pk}-transcription_model": "tiny",
        # We disable translation implicitly by omitting enable_translation
        f"room-{room.pk}-translation_provider": "openai",
        f"room-{room.pk}-translation_model": "gpt-4o",
        # User didn't provide a new key
        f"room-{room.pk}-translation_openai_api_key": "",
        "interpretation_room_action": "save",
        "interpretation_room_id": str(room.pk),
    }

    # Mock room sync success (this happens before API keys sync)
    responses.add(
        responses.PUT,
        f"{base_url}/api/v1/events/{connected_event.slug}/rooms/{room.pk}",
        json={"room_id": str(room.pk)},
        status=200,
    )
    # Mock voxbento api keys sync success
    responses.add(
        responses.PATCH,
        f"{base_url}/api/v1/events/{connected_event.slug}/api_keys",
        status=200,
        json={"status": "ok"},
    )
    response = organizer_client.post(rooms_url, post_data)

    # 1. Save succeeds
    assert response.status_code == 302  # Redirect on success

    # 2. Feature is disabled in DB
    ri = RoomInterpretation.objects.get(room=room)
    assert not ri.enable_translation


@responses.activate
def test_sync_failure_after_local_commit(organizer_client, connected_event, room, rooms_url, voxbento_grant):
    """
    Test that if VoxBento sync fails after a valid key is submitted and local transaction commits,
    the failure is handled gracefully and a warning is shown.
    """
    from interpretation.backends.voxbento_credentials import get_voxbento_base_url

    base_url = get_voxbento_base_url(connected_event)
    # Mock valid provider key validation
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        json={"data": []},
        status=200,
    )
    # Mock token validation
    responses.add(
        responses.POST,
        f"{base_url}/oauth/token",
        json={"access_token": "new", "refresh_token": "new", "expires_in": 3600},
        status=200,
    )
    # Mock voxbento api keys sync FAILURE
    responses.add(
        responses.PATCH,
        f"{base_url}/api/v1/events/{connected_event.slug}/api_keys",
        status=500,
        body="Internal Server Error",
    )
    # Mock room sync success (this happens before API keys sync)
    responses.add(
        responses.PUT,
        f"{base_url}/api/v1/events/{connected_event.slug}/rooms/{room.pk}",
        json={"room_id": str(room.pk)},
        status=200,
    )

    post_data = {
        f"room-{room.pk}-interpreter": "voxbento",
        f"room-{room.pk}-room_enabled": "on",
        f"room-{room.pk}-enable_transcription": "on",
        f"room-{room.pk}-transcription_provider": "local",
        f"room-{room.pk}-transcription_model": "tiny",
        f"room-{room.pk}-enable_translation": "on",
        f"room-{room.pk}-translation_provider": "openai",
        f"room-{room.pk}-translation_model": "gpt-4o",
        f"room-{room.pk}-translation_openai_api_key": "sk-valid-key",
        "interpretation_room_action": "save",
        "interpretation_room_id": str(room.pk),
    }

    response = organizer_client.post(rooms_url, post_data, follow=True)

    # 1. Local transaction committed the new key
    voxbento_grant.refresh_from_db()
    assert voxbento_grant.translation_openai_api_key == "sk-valid-key"

    # 2. Sync failure message shown
    messages = list(response.context["messages"])
    assert any("API key sync failed" in str(m) for m in messages)
