from django.db import migrations


def asegurar_tipos(apps, schema_editor):
    TipoProceso = apps.get_model("trazabilidad", "TipoProceso")
    for codigo, nombre in (
        ("PROCESAMIENTO", "Procesamiento"),
        ("DESCABEZADO", "Descabezado"),
        ("FILETEO", "Fileteo"),
        ("EMPARRILLADO", "Emparrillado"),
    ):
        TipoProceso.objects.using(schema_editor.connection.alias).get_or_create(
            codigo=codigo, defaults={"nombre": nombre, "activo": True},
        )


class Migration(migrations.Migration):
    dependencies = [("trazabilidad", "0004_rutas_procesamiento")]
    # Revertir la estructura no debe borrar catálogos que podrían tener historia.
    operations = [migrations.RunPython(asegurar_tipos, migrations.RunPython.noop)]
