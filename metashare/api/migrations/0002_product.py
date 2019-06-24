# Generated by Django 2.2.2 on 2019-06-24 17:51

import django.contrib.postgres.fields
import hashid_field.field
from django.db import migrations, models

import sfdo_template_helpers.fields


class Migration(migrations.Migration):

    dependencies = [("api", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                (
                    "id",
                    hashid_field.field.HashidAutoField(
                        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
                        min_length=7,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=50, unique=True)),
                ("repo_name", models.SlugField(unique=True)),
                ("version_number", models.CharField(max_length=50)),
                ("description", sfdo_template_helpers.fields.MarkdownField()),
                ("is_managed", models.BooleanField(default=False)),
                (
                    "license",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(
                            choices=[
                                ("mit", "mit"),
                                ("lgpl-3.0", "lgpl-3.0"),
                                ("mpl-2.0", "mpl-2.0"),
                                ("agpl-3.0", "agpl-3.0"),
                                ("unlicense", "unlicense"),
                                ("apache-2.0", "apache-2.0"),
                                ("gpl-3.0", "gpl-3.0"),
                            ],
                            max_length=64,
                        ),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
            ],
            options={"abstract": False},
        )
    ]
