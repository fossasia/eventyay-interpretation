from django.db import migrations

SETTING_SUSI_BASE_URL = "interpretation_susi_base_url"
SETTING_SUSI_AUTH_TOKEN = "interpretation_susi_auth_token"
SETTING_SUSI_ACCOUNT_EMAIL = "interpretation_susi_account_email"
SETTING_SUSI_ACCOUNT_NAME = "interpretation_susi_account_name"

LEGACY_SUSI_BASE_URL = "interpretation_base_url"
LEGACY_SUSI_AUTH_TOKEN = "interpretation_auth_token"
LEGACY_SUSI_EMAIL = "interpretation_susi_email"
LEGACY_SUSI_NAME = "interpretation_susi_name"

ROOM_SUSI_BASE_URL = "susi_base_url"
ROOM_SUSI_AUTH_TOKEN = "susi_auth_token"
ROOM_SUSI_ACCOUNT_EMAIL = "susi_account_email"
ROOM_SUSI_ACCOUNT_NAME = "susi_account_name"

INTERPRETER_CREDENTIAL_KEYS = frozenset(
    {
        ROOM_SUSI_BASE_URL,
        ROOM_SUSI_AUTH_TOKEN,
        ROOM_SUSI_ACCOUNT_EMAIL,
        ROOM_SUSI_ACCOUNT_NAME,
    }
)


def _event_settings_get(event, key, default=""):
    settings = getattr(event, "settings", None)
    if settings is None:
        return default
    return settings.get(key, default=default, as_type=str)


def _event_settings_set(event, key, value):
    settings = getattr(event, "settings", None)
    if settings is not None:
        settings.set(key, value)


def _event_has_susi_token(event) -> bool:
    for key in (SETTING_SUSI_AUTH_TOKEN, LEGACY_SUSI_AUTH_TOKEN):
        if (_event_settings_get(event, key) or "").strip():
            return True
    return False


def _copy_room_credentials_to_event(event, config: dict) -> None:
    base_url = (config.get(ROOM_SUSI_BASE_URL) or "").strip().rstrip("/")
    token = (config.get(ROOM_SUSI_AUTH_TOKEN) or "").strip()
    if not base_url or not token:
        return
    _event_settings_set(event, SETTING_SUSI_BASE_URL, base_url)
    _event_settings_set(event, SETTING_SUSI_AUTH_TOKEN, token)
    _event_settings_set(
        event,
        SETTING_SUSI_ACCOUNT_EMAIL,
        (config.get(ROOM_SUSI_ACCOUNT_EMAIL) or "").strip(),
    )
    _event_settings_set(
        event,
        SETTING_SUSI_ACCOUNT_NAME,
        (config.get(ROOM_SUSI_ACCOUNT_NAME) or "").strip(),
    )


def _migrate_legacy_event_keys(event) -> None:
    legacy_base = (_event_settings_get(event, LEGACY_SUSI_BASE_URL) or "").rstrip("/")
    legacy_token = (_event_settings_get(event, LEGACY_SUSI_AUTH_TOKEN) or "").strip()
    if not legacy_token:
        return
    if not _event_settings_get(event, SETTING_SUSI_AUTH_TOKEN):
        _event_settings_set(event, SETTING_SUSI_BASE_URL, legacy_base)
        _event_settings_set(event, SETTING_SUSI_AUTH_TOKEN, legacy_token)
        _event_settings_set(
            event,
            SETTING_SUSI_ACCOUNT_EMAIL,
            _event_settings_get(event, LEGACY_SUSI_EMAIL),
        )
        _event_settings_set(
            event,
            SETTING_SUSI_ACCOUNT_NAME,
            _event_settings_get(event, LEGACY_SUSI_NAME),
        )


def consolidate_interpreter_credentials_at_event(apps, schema_editor):
    RoomInterpretation = apps.get_model("interpretation", "RoomInterpretation")
    events_seen: set[int] = set()

    for interpretation in RoomInterpretation.objects.select_related("room__event").iterator():
        event = interpretation.room.event
        event_id = event.pk
        config = dict(interpretation.backend_config or {})

        if event_id not in events_seen:
            events_seen.add(event_id)
            _migrate_legacy_event_keys(event)
            if not _event_has_susi_token(event):
                _copy_room_credentials_to_event(event, config)

        if config.keys() & INTERPRETER_CREDENTIAL_KEYS:
            for key in INTERPRETER_CREDENTIAL_KEYS:
                config.pop(key, None)
            interpretation.backend_config = config
            interpretation.save(update_fields=["backend_config"])


class Migration(migrations.Migration):
    dependencies = [
        ("interpretation", "0005_normalize_legacy_session_status"),
    ]

    operations = [
        migrations.RunPython(
            consolidate_interpreter_credentials_at_event,
            migrations.RunPython.noop,
        ),
    ]
