from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.trazabilidad.models import UnidadFrio
from apps.usuarios.models import Usuario
from apps.usuarios.permisos import ROL_JEFE, tiene_rol


@transaction.atomic
def configurar_umbral(*, usuario, unidad, horas):
    if not usuario.is_authenticated:
        raise PermissionDenied
    usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
    if (not usuario.is_active or not tiene_rol(usuario, ROL_JEFE)
            or (usuario.cambio_contrasena_pendiente and not usuario.is_superuser)):
        raise PermissionDenied
    unidad = UnidadFrio.objects.select_for_update().get(pk=unidad.pk)
    unidad.umbral_alerta_horas = horas
    unidad.full_clean()
    unidad.save(update_fields=["umbral_alerta_horas"])
    return unidad
