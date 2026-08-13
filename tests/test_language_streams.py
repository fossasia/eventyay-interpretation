"""Tests for plugin-owned attendee language streams."""

import pytest
from django.core.exceptions import ValidationError

from interpretation.language_streams import (
    attendee_language_streams,
    is_usable_stream_entry,
    is_whep_or_url_source,
    normalize_audio_source,
    validate_language_streams,
)
from interpretation.models import RoomInterpretation
from interpretation.settings import SETTING_USE_PLUGIN_STREAMS
from interpretation.video_integration import augment_room_config

pytestmark = pytest.mark.django_db


def test_normalize_audio_source_youtube_id():
    assert normalize_audio_source("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert (
        normalize_audio_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        == "dQw4w9WgXcQ"
    )


def test_normalize_audio_source_whep_url():
    url = "https://whep.example.com/live/de"
    assert normalize_audio_source(url) == url
    assert is_whep_or_url_source(url) is True


def test_validate_language_streams_rejects_duplicate_language():
    with pytest.raises(ValidationError):
        validate_language_streams(
            [
                {"language": "German", "youtube_id": "https://a.example/whep"},
                {"language": "German", "youtube_id": "https://b.example/whep"},
            ]
        )


def test_attendee_language_streams_includes_original():
    streams = attendee_language_streams(
        [{"language": "German", "youtube_id": "https://whep.example/de"}]
    )
    assert streams[0]["language"] == "Original"
    assert is_usable_stream_entry(streams[1]) is True


def test_augment_room_config_when_flag_enabled(event, room):
    event.plugins = "interpretation"
    event.save(update_fields=["plugins"])
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, True)
    RoomInterpretation.objects.create(
        room=room,
        language_streams=[
            {"language": "French", "youtube_id": "https://whep.example/fr"},
        ],
    )
    config = {}
    augment_room_config(room, config)
    assert config["interpretation_use_plugin_streams"] is True
    streams = config["interpretation_language_streams"]
    assert any(entry["language"] == "French" for entry in streams)


def test_augment_room_config_when_flag_disabled(event, room):
    event.plugins = "interpretation"
    event.save(update_fields=["plugins"])
    RoomInterpretation.objects.create(
        room=room,
        language_streams=[
            {"language": "French", "youtube_id": "https://whep.example/fr"},
        ],
    )
    config = {}
    augment_room_config(room, config)
    assert config["interpretation_use_plugin_streams"] is False
    assert "interpretation_language_streams" not in config


def test_serialize_room_config_includes_plugin_flag(event, room):
    from eventyay.features.live.modules.room import serialize_room_config

    event.plugins = "interpretation"
    event.save(update_fields=["plugins"])
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, True)
    payload = serialize_room_config(room)
    assert payload["interpretation_use_plugin_streams"] is True


def test_get_room_config_includes_plugin_streams(event, room):
    from eventyay.base.services.event import get_room_config

    event.plugins = "interpretation"
    event.save(update_fields=["plugins"])
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, True)
    RoomInterpretation.objects.create(
        room=room,
        language_streams=[
            {"language": "German", "youtube_id": "https://whep.example/de"},
        ],
    )
    config = get_room_config(room, set())
    assert config["interpretation_use_plugin_streams"] is True
    assert any(
        entry["language"] == "German"
        for entry in config["interpretation_language_streams"]
    )


def test_api_streams_endpoint(organizer_client, event, room):
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, True)
    RoomInterpretation.objects.create(
        room=room,
        language_streams=[
            {"language": "German", "youtube_id": "https://whep.example/de"},
        ],
    )
    org = event.organizer.slug
    slug = event.slug
    response = organizer_client.get(
        f"/api/v1/organizers/{org}/events/{slug}/rooms/{room.pk}/interpretation/streams/"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["use_plugin_language_streams"] is True
    assert payload["language_streams"][0]["language"] == "German"
    attendee = payload["attendee_language_streams"]
    assert any(entry["language"] == "Original" for entry in attendee)


def test_api_config_patch_language_streams(organizer_client, event, room):
    event.plugins = "interpretation"
    event.save(update_fields=["plugins"])
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, True)
    RoomInterpretation.objects.create(room=room)
    org = event.organizer.slug
    slug = event.slug
    url = (
        f"/api/v1/organizers/{org}/events/{slug}/rooms/{room.pk}/interpretation/config/"
    )
    response = organizer_client.patch(
        url,
        {
            "language_streams": [
                {
                    "language": "Spanish",
                    "youtube_id": "https://whep.example/es",
                    "use_video": True,
                }
            ]
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["language_streams"][0]["language"] == "Spanish"
    assert payload["language_streams"][0]["use_video"] is True
