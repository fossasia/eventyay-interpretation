from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from ..models import RoomInterpretation
from .base import InterpreterBackend


class ManualBackend(InterpreterBackend):
    id = RoomInterpretation.INTERPRETER_MANUAL
    label = _("Manual / Custom Streams")

    def is_configured(self, event) -> bool:
        return True

    def start(self, event, interpretation, *, stream_url: str) -> str:
        raise ValueError("Cannot start an automated session for manual streams.")

    def stop(self, event, interpretation) -> None:
        return None
