from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("interpretation", "0002_move_connection_to_event_settings"),
    ]

    operations = [
        migrations.RenameField(
            model_name="roominterpretation",
            old_name="hls_url",
            new_name="stream_url",
        ),
    ]
