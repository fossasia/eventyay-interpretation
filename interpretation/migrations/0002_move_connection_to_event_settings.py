from django.db import migrations


def copy_connection_settings_to_event(apps, schema_editor):
    SusiConnection = apps.get_model("interpretation", "SusiConnection")
    if not SusiConnection.objects.exists():
        return

    from eventyay.base.models import Event

    for connection in SusiConnection.objects.iterator():
        event = Event.objects.get(pk=connection.event_id)
        event.settings.set("interpretation_base_url", connection.base_url)
        event.settings.set("interpretation_auth_token", connection.auth_token or "")
        event.settings.set("interpretation_is_enabled", connection.is_enabled)


class Migration(migrations.Migration):

    dependencies = [
        ("interpretation", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            copy_connection_settings_to_event,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="roominterpretation",
            name="connection",
        ),
        migrations.DeleteModel(
            name="SusiConnection",
        ),
    ]
