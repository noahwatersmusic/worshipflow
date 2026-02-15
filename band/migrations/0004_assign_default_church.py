from django.db import migrations


def assign_default_church(apps, schema_editor):
    Church = apps.get_model('band', 'Church')
    Person = apps.get_model('band', 'Person')
    Song = apps.get_model('band', 'Song')
    Service = apps.get_model('band', 'Service')
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('band', 'UserProfile')

    # Create default church
    church, _ = Church.objects.get_or_create(
        slug='default',
        defaults={'name': 'My Church', 'is_active': True}
    )

    # Assign all existing records to the default church
    Person.objects.filter(church__isnull=True).update(church=church)
    Song.objects.filter(church__isnull=True).update(church=church)
    Service.objects.filter(church__isnull=True).update(church=church)

    # Create a superadmin user if none exists
    if not User.objects.filter(is_superuser=True).exists():
        user = User.objects.create_superuser('admin', '', 'admin')
    else:
        user = User.objects.filter(is_superuser=True).first()

    # Create superadmin profile
    UserProfile.objects.get_or_create(
        user=user,
        defaults={'app_role': 'superadmin', 'church': None}
    )


def reverse_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0003_church_and_profiles'),
    ]

    operations = [
        migrations.RunPython(assign_default_church, reverse_migration),
    ]
