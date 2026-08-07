"""Tests for interpreter backend registry plumbing."""

import pytest

from interpretation.backends import (
    get_backend,
    is_registered_interpreter,
    list_configurable_interpreters,
    registered_interpreter_ids,
)
from interpretation.backends.registry import all_room_credential_keys
from interpretation.backends.susi import SusiBackend
from interpretation.models import RoomInterpretation

pytestmark = pytest.mark.django_db


def test_registered_interpreter_ids():
    assert RoomInterpretation.INTERPRETER_SUSI in registered_interpreter_ids()
    assert RoomInterpretation.INTERPRETER_NONE not in registered_interpreter_ids()


def test_is_registered_interpreter():
    assert is_registered_interpreter(RoomInterpretation.INTERPRETER_SUSI) is True
    assert is_registered_interpreter("unknown") is False


def test_susi_backend_exposes_event_credentials():
    backend = get_backend(RoomInterpretation.INTERPRETER_SUSI)
    assert isinstance(backend, SusiBackend)
    assert backend.uses_event_credentials is True
    assert backend.room_credential_keys


def test_list_configurable_interpreters_includes_susi(event):
    ids = {item["id"] for item in list_configurable_interpreters(event)}
    assert RoomInterpretation.INTERPRETER_SUSI in ids


def test_all_room_credential_keys_includes_legacy_susi_keys():
    keys = all_room_credential_keys()
    assert "susi_auth_token" in keys
    assert "susi_base_url" in keys
