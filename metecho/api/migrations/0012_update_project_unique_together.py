# Generated by Django 2.2.4 on 2019-08-30 14:10

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("api", "0011_nullable_branch_names")]

    operations = [
        migrations.AlterUniqueTogether(
            name="project", unique_together={("name", "repository")}
        )
    ]
