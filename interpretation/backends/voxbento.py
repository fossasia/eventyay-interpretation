from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from ..models import RoomInterpretation
from .base import InterpreterBackend
from .voxbento_credentials import (
    clear_voxbento_credentials,
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
