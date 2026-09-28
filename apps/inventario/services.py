from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.trazabilidad.models import Caja, UnidadFrio
from apps.usuarios.permisos import ROL_ENCARGADA, ROL_JEFE, ROL_OPERARIA, tiene_rol
from .models import EstanciaCaja


@transaction.atomic
def _registrar_almacenamiento(*, caja, unidad, usuario, mover):
    if not tiene_rol(usuario, ROL_JEFE, ROL_ENCARGADA, ROL_OPERARIA):
        raise PermissionDenied
    # Todas las altas y movimientos serializan sobre la misma fila estable.
    caja = Caja.objects.select_for_update().get(pk=caja.pk)
    if hasattr(caja, "baja") or hasattr(caja, "detalle_despacho"):
        raise ValidationError("La caja tiene una salida definitiva registrada y no puede almacenarse.")
    abiertas = list(caja.estancias_inventario.filter(fecha_hora_salida__isnull=True).select_related("unidad_frio"))
    if len(abiertas) > 1 or (abiertas and abiertas[0].unidad_frio.tipo != UnidadFrio.TipoUnidad.ALMACENAMIENTO):
        raise ValidationError("El historial de ubicación de esta caja requiere revisión antes de registrar movimientos.")
    if mover and not abiertas:
        raise ValidationError("La caja no posee una ubicación de almacenamiento activa.")
    if not mover and abiertas:
        raise ValidationError("La caja ya se encuentra almacenada.")
    destino = UnidadFrio.objects.select_for_update().filter(pk=unidad.pk, tipo=UnidadFrio.TipoUnidad.ALMACENAMIENTO, activo=True).first()
    if destino is None:
        raise ValidationError("La unidad seleccionada no está disponible para almacenamiento.")
    ahora = timezone.now()
    if mover:
        anterior = abiertas[0]
        if anterior.unidad_frio_id == destino.pk:
            raise ValidationError("La caja ya se encuentra en esa cámara.")
        if anterior.fecha_hora_ingreso > ahora:
            raise ValidationError("La fecha de ingreso registrada requiere revisión antes de mover la caja.")
        anterior.fecha_hora_salida = ahora
        anterior.retirado_por = usuario
        anterior.save(update_fields=["fecha_hora_salida", "retirado_por"])
    return EstanciaCaja.objects.create(caja=caja, unidad_frio=destino, fecha_hora_ingreso=ahora, ingresado_por=usuario)


def ingresar_caja(*, caja, unidad, usuario):
    return _registrar_almacenamiento(caja=caja, unidad=unidad, usuario=usuario, mover=False)


def mover_caja(*, caja, unidad, usuario):
    return _registrar_almacenamiento(caja=caja, unidad=unidad, usuario=usuario, mover=True)
