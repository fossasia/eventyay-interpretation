from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("interpretation", "0006_move_credentials_to_rooms"),
    ]

    operations = [
        migrations.AlterField(
            model_name="roominterpretation",
            name="interpreter",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("susi", "SUSI Translator"),
                    ("voxbento", "VoxBento Console"),
                ],
                default="none",
                max_length=32,
                verbose_name="Interpreter",
            ),
        ),
    ]
