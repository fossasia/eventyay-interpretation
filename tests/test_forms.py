"""Tests for interpretation forms."""

from interpretation.forms import (
    InterpretationSettingsForm,
)
from interpretation.settings import (
    SETTING_IS_ENABLED,
    SETTING_USE_PLUGIN_STREAMS,
)


class _FakeSettings:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None, as_type=None):
        val = self._data.get(key, default)
        if as_type is bool:
            return str(val).lower() in ("true", "1", "yes")
        return val


class _FakeEvent:
    id = 1
    pk = 1

    def __int__(self):
        return self.id

    def __init__(self, settings=None):
        self.settings = _FakeSettings(settings)


def test_interpretation_settings_form_initial():
    event = _FakeEvent({SETTING_IS_ENABLED: True})
    form = InterpretationSettingsForm(obj=event)
    assert form.fields["interpretation_is_enabled"].initial is True


def test_interpretation_settings_form_save():
    settings = {}
    data = {SETTING_IS_ENABLED: True, SETTING_USE_PLUGIN_STREAMS: False}
    prefix = "interpretation"
    post = {f"{prefix}-{key}": value for key, value in data.items()}
    return InterpretationSettingsForm(obj=_FakeEvent(settings), data=post, prefix=prefix)
