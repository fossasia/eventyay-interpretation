"""Persist VoxBento booth URLs in both supported room configuration modes."""

from unittest.mock import Mock

import pytest
from eventyay.base.models import Room

from interpretation.models import RoomInterpretation, VoxbentoOAuthGrant
from interpretation.settings import SETTING_USE_PLUGIN_STREAMS
from interpretation.tasks import _do_sync_single_room_to_voxbento

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("module_type", ["livestream.youtube", "livestream.native"])
@pytest.mark.parametrize("booth_format", ["dict", "list"])
def test_sync_room_updates_module_language_urls(event, room, monkeypatch, module_type, booth_format):
    monkeypatch.setattr("interpretation.signals._do_sync_single_room_to_voxbento", lambda *args, **kwargs: False)
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, False)
    Room.objects.filter(pk=room.pk).update(
        module_config=[
            {
                "type": module_type,
                "config": {
                    "languageUrls": [
                        {"language": "German", "youtube_id": ""},
                        {"language": "French", "youtube_id": "https://old.example/fr/whep"},
                        {"language": "Spanish", "youtube_id": "https://external.example/es"},
                    ]
                },
            }
        ]
    )
    VoxbentoOAuthGrant.objects.create(event=event, access_token="test-token")
    urls = {code: f"https://voxbento.example/{code}/whep" for code in ["de", "fr", "es"]}
    booths = (
        urls if booth_format == "dict" else [{"language_code": code, "whep_url": url} for code, url in urls.items()]
    )
    sync = Mock(return_value={"booths": booths})
    monkeypatch.setattr("interpretation.tasks.sync_voxbento_room", sync)
    monkeypatch.setattr("interpretation.tasks.get_voxbento_base_url", lambda event: "https://voxbento.example")

    assert _do_sync_single_room_to_voxbento(room.pk, event.pk, "upsert") is False

    room.refresh_from_db()
    assert room.module_config[0]["config"]["languageUrls"] == [
        {"language": "German", "youtube_id": urls["de"]},
        {"language": "French", "youtube_id": urls["fr"]},
        {"language": "Spanish", "youtube_id": "https://external.example/es"},
    ]
    assert set(sync.call_args.args[2]["target_languages"]) == {"de", "fr", "es"}


def test_sync_room_updates_plugin_language_streams(event, room, monkeypatch):
    monkeypatch.setattr("interpretation.signals._do_sync_single_room_to_voxbento", lambda *args, **kwargs: False)
    event.settings.set(SETTING_USE_PLUGIN_STREAMS, True)
    interpretation = RoomInterpretation.objects.create(
        room=room,
        interpreter="voxbento",
        room_enabled=True,
        target_languages=["de"],
        language_streams=[{"language": "German", "youtube_id": ""}],
    )
    VoxbentoOAuthGrant.objects.create(event=event, access_token="test-token")
    url = "https://voxbento.example/de/whep"
    monkeypatch.setattr("interpretation.tasks.sync_voxbento_room", Mock(return_value={"booths": {"de": url}}))
    monkeypatch.setattr("interpretation.tasks.get_voxbento_base_url", lambda event: "https://voxbento.example")

    assert _do_sync_single_room_to_voxbento(room.pk, event.pk, "upsert") is False

    interpretation.refresh_from_db()
    assert interpretation.language_streams == [{"language": "German", "youtube_id": url}]
