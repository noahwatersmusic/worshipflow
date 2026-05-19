from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0008_servicemember'),
    ]

    operations = [
        migrations.AddField(
            model_name='church',
            name='pco_app_id',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='church',
            name='pco_secret',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
