from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("interpretation", "0003_rename_hls_url_stream_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="roominterpretation",
            name="room_enabled",
            field=models.BooleanField(
                default=False,
                verbose_name="Interpretation enabled for room",
            ),
        ),
    ]
