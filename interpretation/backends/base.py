from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from eventyay.base.models import Event

    from ..models import RoomInterpretation


class InterpreterBackend(Protocol):
    id: str
    label: str

    def is_configured(self, interpretation: RoomInterpretation | None) -> bool: ...

    def start(
        self,
        event: Event,
        interpretation: RoomInterpretation,
        *,
        stream_url: str,
    ) -> str: ...

    def stop(self, event: Event, interpretation: RoomInterpretation) -> None: ...
