from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("interpretation", "0007_voxbento_interpreter"),
    ]

    operations = [
        migrations.AddField(
            model_name="roominterpretation",
            name="language_streams",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Attendee audio translation channels (YouTube ID or WHEP URL per language).",
                verbose_name="Language streams",
            ),
        ),
    ]
