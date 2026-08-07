from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from ..models import RoomInterpretation
from ..services import start_stream_session
from ..susi import SusiError
from .base import InterpreterBackend
from .susi_credentials import (
    ROOM_CREDENTIAL_KEYS,
    clear_susi_credentials,
    get_susi_client,
    is_susi_configured,
    susi_account_label,
    susi_server_host,
)


class SusiBackend(InterpreterBackend):
    id = RoomInterpretation.INTERPRETER_SUSI
    label = _("SUSI Translator")
    uses_event_credentials = True
    room_credential_keys = ROOM_CREDENTIAL_KEYS

    def is_configured(self, event) -> bool:
        return is_susi_configured(event)

    def start(self, event, interpretation, *, stream_url: str) -> str:
        client = get_susi_client(event)
        return start_stream_session(
            client,
            stream_url,
            transcription_provider=interpretation.transcription_provider,
            translation_provider=interpretation.translation_provider,
            source_language=interpretation.source_language,
            target_languages=list(interpretation.target_languages or []),
        )

    def stop(self, event, interpretation) -> None:
        session_id = interpretation.backend_session_id
        if not session_id:
            return
        client = get_susi_client(event)
        try:
            result = client.stop_session(session_id)
        except SusiError:
            raise
        if not result.ok:
            raise SusiError(f"Failed to stop SUSI session: {result.data}")

    def build_credentials_form(self, event, data=None):
        from ..forms import SusiInterpreterCredentialsForm

        return SusiInterpreterCredentialsForm(data=data, event=event)

    def connect(self, request, event, post_data):
        from ..forms import CONNECT_POST_KEY

        post = post_data.copy()
        post[CONNECT_POST_KEY] = "1"
        form = self.build_credentials_form(event, data=post)
        if not form.is_valid():
            return form, False
        form.run_connect_action(request, event)
        return form, True

    def test_connection(self, request, event) -> None:
        from ..forms import verify_susi_connection

        verify_susi_connection(event, request)

    def disconnect(self, event) -> None:
        clear_susi_credentials(event)

    def credentials_account_label(self, event) -> str:
        return susi_account_label(event)

    def credentials_server_label(self, event) -> str:
        return susi_server_host(event)
