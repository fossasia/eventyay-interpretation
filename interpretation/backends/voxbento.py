from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from ..models import RoomInterpretation
from .base import InterpreterBackend
from .voxbento_credentials import (
    clear_voxbento_credentials,
    get_voxbento_api_key,
    get_voxbento_base_url,
    is_voxbento_configured,
    voxbento_server_host,
)


class VoxbentoBackend(InterpreterBackend):
    id = RoomInterpretation.INTERPRETER_VOXBENTO
    label = _("VoxBento Console")
    uses_event_credentials = True
    room_credential_keys = frozenset()

    def is_configured(self, event) -> bool:
        return is_voxbento_configured(event)

    def start(self, event, interpretation, *, stream_url: str) -> str:
        # VoxBento sessions are persistent "booths" identified by the event slug
        # and language code, managed by the VoxBento server directly.
        # We don't need to push stream ingest to VoxBento from Eventyay because
        # VoxBento is an interpreter console where interpreters speak directly.
        # So starting the session here just marks the room as active.
        return f"{event.slug}-{interpretation.room_id}"

    def sync_booths(self, event, interpretation) -> None:
        if not interpretation.target_languages:
            return

        base_url = get_voxbento_base_url(event)
        api_key = get_voxbento_api_key(event)
        if not base_url or not api_key:
            return

        url = f"{base_url.rstrip('/')}/api/events/{event.slug}/booths"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        config = interpretation.backend_config.copy()
        booths = config.get("booths", {})

        import requests

        for lang in interpretation.target_languages:
            payload = {
                "language_code": lang,
                "language": lang.upper(),
                "room_id": interpretation.room_id,
            }
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=5.0
                )
                if response.ok:
                    data = response.json()
                    booths[lang] = {
                        "invite_url": data.get("interpreter_invite_url"),
                        "caption_url": data.get("caption_url"),
                        "whip_url": data.get("whip_url"),
                        "whep_url": data.get("whep_url"),
                    }
            except requests.RequestException:
                pass

        config["booths"] = booths
        interpretation.backend_config = config
        interpretation.save(update_fields=["backend_config"])

    def stop(self, event, interpretation) -> None:
        # Stopping just marks it inactive on Eventyay side.
        pass

    def build_credentials_form(self, event, data=None):
        from ..forms import VoxbentoInterpreterCredentialsForm

        return VoxbentoInterpreterCredentialsForm(data=data, event=event)

    def connect(self, request, event, post_data):
        from ..forms import CONNECT_POST_KEY

        post = post_data.copy()
        post[CONNECT_POST_KEY] = "1"
        form = self.build_credentials_form(event, data=post)
        if not form.is_valid():
            return form, False
        success = form.run_connect_action(request, event)
        return form, success

    def test_connection(self, request, event) -> None:
        from ..forms import verify_voxbento_connection

        verify_voxbento_connection(event, request)

    def disconnect(self, event) -> None:
        clear_voxbento_credentials(event)

    def credentials_account_label(self, event) -> str:
        # VoxBento doesn't use "accounts", just API keys.
        return _("VoxBento API Key")

    def credentials_server_label(self, event) -> str:
        return voxbento_server_host(event)
