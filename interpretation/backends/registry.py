from __future__ import annotations

from .none import NoopBackend
from .susi import SusiBackend

INTERPRETER_NONE = NoopBackend.id
INTERPRETER_SUSI = SusiBackend.id

_BACKENDS = {
    INTERPRETER_NONE: NoopBackend(),
    INTERPRETER_SUSI: SusiBackend(),
}


def get_backend(interpreter_id: str):
    return _BACKENDS.get(interpreter_id, _BACKENDS[INTERPRETER_NONE])


def list_available_interpreters(interpretation=None) -> list[dict]:
    return [
        {
            "id": backend.id,
            "label": str(backend.label),
            "configured": backend.is_configured(interpretation),
        }
        for backend in _BACKENDS.values()
    ]
