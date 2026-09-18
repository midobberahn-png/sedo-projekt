from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sedo', '0015_offer_follow_up'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('text', models.TextField(max_length=4000, verbose_name='Notiz')),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sedo_customer_notes', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notes', to='sedo.customer')),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
                'indexes': [models.Index(fields=['customer', '-created_at'], name='custnote_customer_date_idx')],
            },
        ),
    ]
