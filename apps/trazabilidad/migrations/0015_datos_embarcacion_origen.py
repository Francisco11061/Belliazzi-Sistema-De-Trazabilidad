from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trazabilidad", "0014_solicitudes_rutas")]

    operations = [
        migrations.AddField(
            model_name="origensernapesca", name="nombre_embarcacion",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="origensernapesca", name="matricula_embarcacion",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="origensernapesca", name="rpa_embarcacion",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="origensernapesca", name="caleta_desembarque",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="origensernapesca", name="fecha_desembarque",
            field=models.DateField(blank=True, null=True),
        ),
    ]
