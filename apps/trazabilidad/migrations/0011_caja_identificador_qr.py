import uuid

from django.db import migrations, models


def asignar_identificadores(apps, schema_editor):
    cajas = apps.get_model("trazabilidad", "Caja").objects.using(schema_editor.connection.alias)
    for pk in cajas.filter(identificador_qr__isnull=True).values_list("pk", flat=True).iterator():
        cajas.filter(pk=pk, identificador_qr__isnull=True).update(identificador_qr=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("trazabilidad", "0010_caja_peso_neto_kg")]
    operations = [
        migrations.AddField(
            model_name="caja", name="identificador_qr",
            field=models.UUIDField(null=True, unique=True, editable=False),
        ),
        migrations.RunPython(asignar_identificadores, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="caja", name="identificador_qr",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
