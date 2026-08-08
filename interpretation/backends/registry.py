from __future__ import annotations

from .base import InterpreterBackend
from .none import NoopBackend
from .susi import SusiBackend
from .voxbento import VoxbentoBackend

INTERPRETER_NONE = NoopBackend.id
INTERPRETER_SUSI = SusiBackend.id
INTERPRETER_VOXBENTO = VoxbentoBackend.id

_BACKENDS: dict[str, InterpreterBackend] = {
    INTERPRETER_NONE: NoopBackend(),
    INTERPRETER_SUSI: SusiBackend(),
    INTERPRETER_VOXBENTO: VoxbentoBackend(),
}


def get_backend(interpreter_id: str) -> InterpreterBackend:
    return _BACKENDS.get(interpreter_id, _BACKENDS[INTERPRETER_NONE])


def iter_backends() -> list[InterpreterBackend]:
    return list(_BACKENDS.values())


def registered_interpreter_ids() -> frozenset[str]:
    return frozenset(_BACKENDS) - {INTERPRETER_NONE}


def is_known_interpreter(interpreter_id: str) -> bool:
    return interpreter_id in _BACKENDS


def is_registered_interpreter(interpreter_id: str) -> bool:
    return interpreter_id in registered_interpreter_ids()


def all_room_credential_keys() -> frozenset[str]:
    keys: set[str] = set()
    for backend in _BACKENDS.values():
        keys.update(backend.room_credential_keys)
    return frozenset(keys)


def list_available_interpreters(event=None) -> list[dict]:
    return [
        {
            "id": backend.id,
            "label": str(backend.label),
            "configured": backend.is_configured(event),
            "uses_event_credentials": backend.uses_event_credentials,
        }
        for backend in _BACKENDS.values()
    ]


def list_configurable_interpreters(event=None) -> list[dict]:
    return [
        item
        for item in list_available_interpreters(event)
        if item["id"] != INTERPRETER_NONE and item["uses_event_credentials"]
    ]
