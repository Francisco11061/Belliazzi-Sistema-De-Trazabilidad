"""Quién puede corregir un registro.

El jefe corrige siempre. La encargada corrige solo sus propios registros y solo
el mismo día en que los creó; después, la corrección queda en manos del jefe.
"""
from django.utils import timezone

from apps.usuarios.permisos import ROL_ENCARGADA, ROL_JEFE, tiene_rol


def puede_corregir(usuario, autor_id, creado_en):
    if not getattr(usuario, "is_authenticated", False):
        return False
    if tiene_rol(usuario, ROL_JEFE):
        return True
    return bool(
        tiene_rol(usuario, ROL_ENCARGADA) and autor_id == usuario.pk and creado_en
        and timezone.localdate(creado_en) == timezone.localdate()
    )
