from django.db import migrations

SUSI_BASE_URL = "susi_base_url"
SUSI_AUTH_TOKEN = "susi_auth_token"
SUSI_ACCOUNT_EMAIL = "susi_account_email"
SUSI_ACCOUNT_NAME = "susi_account_name"

LEGACY_BASE_URL = "interpretation_base_url"
LEGACY_AUTH_TOKEN = "interpretation_auth_token"
LEGACY_SUSI_EMAIL = "interpretation_susi_email"
LEGACY_SUSI_NAME = "interpretation_susi_name"


def _event_settings_get(event, key, default=""):
    # ponytail: migration-only read; hierarkey API not available here.
    settings = getattr(event, "settings", None)
    if settings is None:
        return default
    return settings.get(key, default=default, as_type=str)


def copy_event_credentials_to_rooms(apps, schema_editor):
    RoomInterpretation = apps.get_model("interpretation", "RoomInterpretation")

    for interpretation in RoomInterpretation.objects.select_related("room").iterator():
        event = interpretation.room.event
        token = _event_settings_get(event, LEGACY_AUTH_TOKEN)
        base_url = (_event_settings_get(event, LEGACY_BASE_URL) or "").rstrip("/")
        if not base_url or not token:
            continue

        config = dict(interpretation.backend_config or {})
        if config.get(SUSI_AUTH_TOKEN):
            continue

        config[SUSI_BASE_URL] = base_url
        config[SUSI_AUTH_TOKEN] = token
        config[SUSI_ACCOUNT_EMAIL] = _event_settings_get(event, LEGACY_SUSI_EMAIL)
        config[SUSI_ACCOUNT_NAME] = _event_settings_get(event, LEGACY_SUSI_NAME)
        interpretation.backend_config = config
        interpretation.save(update_fields=["backend_config"])


class Migration(migrations.Migration):
    dependencies = [
        ("interpretation", "0005_normalize_legacy_session_status"),
    ]

    operations = [
        migrations.RunPython(
            copy_event_credentials_to_rooms,
            migrations.RunPython.noop,
        ),
    ]
