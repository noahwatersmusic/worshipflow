from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('band', '0004_assign_default_church'),
    ]

    operations = [
        migrations.AlterField(
            model_name='person',
            name='church',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='people',
                to='band.church',
            ),
        ),
        migrations.AlterField(
            model_name='song',
            name='church',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='songs',
                to='band.church',
            ),
        ),
        migrations.AlterField(
            model_name='service',
            name='church',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='services',
                to='band.church',
            ),
        ),
    ]
