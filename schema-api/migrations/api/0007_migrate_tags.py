# Generated by Django 5.0.4 on 2024-09-04 11:06

from django.db import migrations


class Migration(migrations.Migration):

    def migrate_tags_to_temp_tag(apps, schema_editor):
        # Get the Tag and TempTag models
        Tag = apps.get_model('api', 'Tag')
        TempTag = apps.get_model('api', 'TempTag')
        Task = apps.get_model('api', 'Task')  # Assuming Task model is in the same app

        # Get all distinct values from Tag.value
        distinct_values = Tag.objects.values_list('value', flat=True).distinct()

        for value in distinct_values:
            # Get all tasks related to this specific value
            related_tasks = Tag.objects.filter(value=value).values_list('task', flat=True)

            # Create a new TempTag object with the distinct value
            temp_tag = TempTag.objects.create(value=value)

            # Add all related tasks to the Many-to-Many relationship in TempTag
            temp_tag.tasks.add(*related_tasks)

    def reverse_migrate_tags_to_temp_tag(apps, schema_editor):
        # Reverse operation: We could delete the TempTag records if needed
        TempTag = apps.get_model('api', 'TempTag')
        TempTag.objects.all().delete()

    dependencies = [
        ('api', '0006_temptag_temptag_tag_value_unique_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_tags_to_temp_tag, reverse_migrate_tags_to_temp_tag),
    ]
