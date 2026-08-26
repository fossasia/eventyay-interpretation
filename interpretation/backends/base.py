from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eventyay.base.models import Event

    from ..models import RoomInterpretation


class InterpreterBackend:
    """Base class for interpreter backends registered in the plugin."""

    id: str = ""
    label: str = ""
    uses_event_credentials: bool = False
    room_credential_keys: frozenset[str] = frozenset()

    def is_configured(self, event: Event) -> bool:
        raise NotImplementedError

    def start(
        self,
        event: Event,
        interpretation: RoomInterpretation,
        *,
        stream_url: str,
    ) -> str:
        raise NotImplementedError

    def stop(self, event: Event, interpretation: RoomInterpretation) -> None:
        raise NotImplementedError

    def build_credentials_form(self, event: Event, data=None):
        return None

    def connect(self, request, event: Event, post_data) -> tuple[object | None, bool]:
        """Return (form_or_none, success)."""
        raise NotImplementedError

    def test_connection(self, request, event: Event) -> None:
        raise NotImplementedError

    def disconnect(self, event: Event) -> None:
        raise NotImplementedError

    def is_disconnected(self, event: Event) -> bool:
        """Returns True if the interpreter has a preserved but disconnected state."""
        return False

    def credentials_account_label(self, event: Event) -> str:
        return ""

    def credentials_server_label(self, event: Event) -> str:
        return ""
