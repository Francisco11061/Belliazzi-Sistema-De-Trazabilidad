from django.db import migrations


def crear_roles_iniciales(apps, schema_editor):
    Rol = apps.get_model("usuarios", "Rol")
    database_alias = schema_editor.connection.alias
    roles = (
        (
            "JEFE",
            "Jefe / Administrador",
            "Responsable de la administración y supervisión general del sistema.",
        ),
        (
            "ENCARGADA",
            "Encargada de registro",
            "Responsable principal del registro y seguimiento operativo.",
        ),
        (
            "OPERARIA",
            "Operaria",
            "Responsable de apoyar el registro de actividades del proceso productivo.",
        ),
    )

    for codigo, nombre, descripcion in roles:
        Rol.objects.using(database_alias).update_or_create(
            codigo=codigo,
            defaults={
                "nombre": nombre,
                "descripcion": descripcion,
                "activo": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("usuarios", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_roles_iniciales, migrations.RunPython.noop),
    ]
