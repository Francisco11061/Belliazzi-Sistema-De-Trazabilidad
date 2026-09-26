from decimal import Decimal

from django.db import migrations


def crear_presentaciones(apps, schema_editor):
    presentacion = apps.get_model("trazabilidad", "PresentacionBolsa")
    for nombre, peso in (("Bolsa 1 kg", "1.00"), ("Bolsa 5 kg", "5.00")):
        presentacion.objects.using(schema_editor.connection.alias).get_or_create(
            nombre=nombre, defaults={"peso_nominal_kg": Decimal(peso), "activo": True},
        )


class Migration(migrations.Migration):
    dependencies = [("trazabilidad", "0008_alter_caja_codigo_caja_alter_caja_peso_total_kg_and_more")]
    operations = [migrations.RunPython(crear_presentaciones, migrations.RunPython.noop)]
