from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from ..models import RoomInterpretation


class NoopBackend:
    id = RoomInterpretation.INTERPRETER_NONE
    label = _("None")

    def is_configured(self, interpretation) -> bool:
        return True

    def start(self, event, interpretation, *, stream_url: str) -> str:
        raise ValueError("Cannot start a session without an interpreter.")

    def stop(self, event, interpretation) -> None:
        return None
