"""Tests for event-level and per-room interpretation forms."""

from interpretation.forms import (
    InterpretationSettingsForm,
)
from interpretation.settings import SETTING_IS_ENABLED

PUBLIC_URL = "https://example.com"


class _FakeHierarkey:
    defaults = {}

    def get_declared_type(self, key):
        return bool if key == SETTING_IS_ENABLED else str


class _FakeSettings:
    def __init__(self, data=None):
        self._data = dict(data or {})
        self._parent = None
        self._h = _FakeHierarkey()

    def get(self, key, default=None, as_type=str):
        if key not in self._data:
            return default
        value = self._data[key]
        if as_type is bool:
            return bool(value)
        return as_type(value)

    def freeze(self):
        return self._data.copy()

    def set(self, key, value):
        self._data[key] = value

    def _cache(self):
        return self._data.keys()


class _FakeEvent:
    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


def _event_form(data, settings=None, prefix="interpretation"):
    post = {f"{prefix}-{key}": value for key, value in data.items()}
    return InterpretationSettingsForm(obj=_FakeEvent(settings), data=post, prefix=prefix)


def test_event_enable_toggle_can_be_saved(monkeypatch):
    monkeypatch.setattr(
        "interpretation.room_control.stop_all_event_sessions",
        lambda event: None,
    )
    form = _event_form({SETTING_IS_ENABLED: False})
    assert form.is_valid(), form.errors
    form.save()
    assert form.obj.settings.get(SETTING_IS_ENABLED, as_type=bool) is False


def test_event_disable_stops_sessions(monkeypatch):
    stopped = []

    def fake_stop_all(event):
        stopped.append(event)

    monkeypatch.setattr(
        "interpretation.room_control.stop_all_event_sessions",
        fake_stop_all,
    )
    form = _event_form(
        {SETTING_IS_ENABLED: False},
        settings={SETTING_IS_ENABLED: True},
    )
    assert form.is_valid(), form.errors
    form.save()
    assert stopped == [form.obj]
