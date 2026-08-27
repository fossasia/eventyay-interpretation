from __future__ import annotations

import logging

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
from .voxbento_oauth import (
    VoxbentoReauthorizationRequired,
    get_valid_access_token,
)

logger = logging.getLogger(__name__)


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

    def sync_booths(self, event, interpretation) -> int:
        from interpretation.tasks import _do_sync_single_room_to_voxbento, ActiveSessionConflict
        from .voxbento_oauth import VoxbentoTemporarilyUnavailable
        
        try:
            needs_retry = _do_sync_single_room_to_voxbento(
                interpretation.room_id,
                event.id,
                "upsert",
                room_instance=interpretation.room
            )
            if needs_retry:
                raise VoxbentoTemporarilyUnavailable("Network error connecting to VoxBento API")
            return len(interpretation.target_languages) if interpretation.target_languages else 1
        except ActiveSessionConflict as e:
            raise Exception(str(e)) from e
        except Exception as e:
            raise Exception(str(e)) from e

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

    def is_disconnected(self, event) -> bool:
        from ..models import VoxbentoOAuthGrant

        try:
            grant = VoxbentoOAuthGrant.objects.get(event=event)
            return grant.is_disconnected or not bool(grant.access_token)
        except VoxbentoOAuthGrant.DoesNotExist:
            return False

    def credentials_account_label(self, event) -> str:
        from ..models import VoxbentoOAuthGrant

        if VoxbentoOAuthGrant.objects.filter(event=event).exists():
            return _("VoxBento OAuth Connection")
        return _("VoxBento API Key")

    def credentials_server_label(self, event) -> str:
        return voxbento_server_host(event)
