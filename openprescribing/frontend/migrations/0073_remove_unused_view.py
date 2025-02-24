# -*- coding: utf-8 -*-


from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("frontend", "0072_remove_unused_models"),
    ]

    operations = [
        migrations.RunSQL("DROP MATERIALIZED VIEW IF EXISTS vw__medians_for_tariff"),
    ]
