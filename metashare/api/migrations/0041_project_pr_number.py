# Generated by Django 2.2.8 on 2019-12-05 20:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0040_project_currently_creating_pr"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="pr_number",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
