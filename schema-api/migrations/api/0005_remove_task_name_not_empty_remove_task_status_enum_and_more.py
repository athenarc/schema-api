# Generated by Django 5.0.4 on 2024-08-11 19:52

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0004_migrate_status'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='task',
            name='name_not_empty',
        ),
        migrations.RemoveConstraint(
            model_name='task',
            name='status_enum',
        ),
        migrations.RemoveConstraint(
            model_name='task',
            name='scheduled_task_task_id_required',
        ),
        migrations.RemoveConstraint(
            model_name='task',
            name='task_status_is_pending_integrity_check',
        ),
        migrations.AlterUniqueTogether(
            name='statushistorypoint',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='task',
            name='status',
        ),
        migrations.AlterField(
            model_name='statushistorypoint',
            name='task',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_history_points', to='api.task'),
        ),
    ]
