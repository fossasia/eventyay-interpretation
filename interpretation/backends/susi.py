from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from ..backend_credentials import get_susi_client, is_susi_configured
from ..models import RoomInterpretation
from ..services import start_stream_session
from ..susi import SusiError


class SusiBackend:
    id = RoomInterpretation.INTERPRETER_SUSI
    label = _("SUSI Translator")

    def is_configured(self, interpretation) -> bool:
        return is_susi_configured(interpretation)

    def start(self, event, interpretation, *, stream_url: str) -> str:
        client = get_susi_client(interpretation)
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
        client = get_susi_client(interpretation)
        try:
            result = client.stop_session(session_id)
        except SusiError:
            raise
        if not result.ok:
            raise SusiError(f"Failed to stop SUSI session: {result.data}")
