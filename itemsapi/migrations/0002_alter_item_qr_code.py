# Generated by Django 5.1.6 on 2025-02-10 07:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('itemsapi', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='item',
            name='qr_code',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
    ]
