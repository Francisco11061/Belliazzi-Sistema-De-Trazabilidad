from django.db import migrations, models
from django.db.models import F


def marcar_resueltas_como_vistas(apps, schema_editor):
    # Las respuestas anteriores a la campana no deben aparecer como nuevas.
    SolicitudRuta = apps.get_model("trazabilidad", "SolicitudRuta")
    SolicitudRuta.objects.exclude(estado="PENDIENTE").update(respuesta_vista_en=F("resuelto_en"))


class Migration(migrations.Migration):

    dependencies = [
        ("trazabilidad", "0018_lote_terminado_y_producto_terminado"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitudruta",
            name="respuesta_vista_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(marcar_resueltas_como_vistas, migrations.RunPython.noop),
    ]
