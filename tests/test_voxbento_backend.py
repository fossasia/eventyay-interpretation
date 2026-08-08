import pytest

from interpretation.backends.registry import (
    INTERPRETER_VOXBENTO,
    get_backend,
    is_registered_interpreter,
)
from interpretation.backends.voxbento import VoxbentoBackend
from interpretation.backends.voxbento_credentials import (
    clear_voxbento_credentials,
    get_voxbento_api_key,
    get_voxbento_base_url,
    is_voxbento_configured,
    save_voxbento_credentials,
    voxbento_server_host,
)


@pytest.fixture
def empty_event(event):
    """An event with no credentials."""
    return event


def test_voxbento_backend_is_registered():
    assert is_registered_interpreter(INTERPRETER_VOXBENTO)
    backend = get_backend(INTERPRETER_VOXBENTO)
    assert isinstance(backend, VoxbentoBackend)
    assert backend.id == INTERPRETER_VOXBENTO


def test_voxbento_credentials_facade(empty_event):
    assert not is_voxbento_configured(empty_event)

    save_voxbento_credentials(empty_event, "https://voxbento.test", "vb_12345")

    assert is_voxbento_configured(empty_event)
    assert get_voxbento_base_url(empty_event) == "https://voxbento.test"
    assert get_voxbento_api_key(empty_event) == "vb_12345"
    assert voxbento_server_host(empty_event) == "voxbento.test"

    clear_voxbento_credentials(empty_event)
    assert not is_voxbento_configured(empty_event)
    assert get_voxbento_base_url(empty_event) == ""
    assert get_voxbento_api_key(empty_event) == ""


def test_voxbento_backend_start(empty_event):
    backend = get_backend(INTERPRETER_VOXBENTO)

    class DummyInterpretation:
        room_id = 42

    empty_event.slug = "my-event"
    interpretation = DummyInterpretation()

    # The start method for voxbento should return a composite ID
    session_id = backend.start(empty_event, interpretation, stream_url="")
    assert session_id == "my-event-42"
