from django.db import migrations


def crear_bodega_por_defecto(apps, schema_editor):
    """Una sola bodega para toda la materia prima, salvo que ya exista otra por defecto."""
    BodegaSernapesca = apps.get_model("trazabilidad", "BodegaSernapesca")
    if not BodegaSernapesca.objects.filter(por_defecto=True).exists():
        BodegaSernapesca.objects.update_or_create(
            nombre="Almacenamiento", defaults={"por_defecto": True, "activo": True},
        )


class Migration(migrations.Migration):
    dependencies = [("trazabilidad", "0016_bodega_sernapesca_y_codigo_recurso")]

    operations = [migrations.RunPython(crear_bodega_por_defecto, migrations.RunPython.noop)]
