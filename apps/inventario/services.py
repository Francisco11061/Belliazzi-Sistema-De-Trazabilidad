from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.trazabilidad.models import Caja, UnidadFrio
from apps.usuarios.permisos import ROL_ENCARGADA, ROL_JEFE, ROL_OPERARIA, tiene_rol
from .models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from .despachos import validar_datos_despacho
from .selectors import cajas_con_inventario


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


@transaction.atomic
def registrar_despacho(*, cajas, usuario, tipo_destino, destino="", rut_destinatario="", pais_destino="",
                       tipo_documento="", numero_documento="", fecha_documento=None, observaciones=""):
    if not tiene_rol(usuario, ROL_JEFE, ROL_ENCARGADA, ROL_OPERARIA):
        raise PermissionDenied
    ids = [c.pk if isinstance(c, Caja) else c for c in cajas]
    if not ids:
        raise ValidationError("Seleccione al menos una caja.")
    if any(type(pk) is not int or pk <= 0 for pk in ids):
        raise ValidationError("Una o más cajas ya no están disponibles para despacho.")
    if len(ids) != len(set(ids)):
        raise ValidationError("No puede seleccionar una caja más de una vez.")
    # Mismo bloqueo que almacenamiento. Orden estable para selecciones superpuestas.
    bloqueadas = list(Caja.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    actuales = list(cajas_con_inventario().filter(pk__in=ids))
    if len(bloqueadas) != len(ids) or any(c.estado_inventario != "ALMACENADA" for c in actuales):
        raise ValidationError("Una o más cajas ya no están disponibles para despacho.")
    if any(c.peso_neto_kg is None for c in bloqueadas):
        raise ValidationError("La caja no puede despacharse porque no posee un peso neto real documentado.")
    ahora = timezone.now()
    estancias = list(EstanciaCaja.objects.filter(caja_id__in=ids, fecha_hora_salida__isnull=True))
    if any(e.fecha_hora_ingreso > ahora for e in estancias):
        raise ValidationError("Una fecha de ingreso de almacenamiento requiere revisión antes de despachar.")
    datos = validar_datos_despacho(dict(tipo_destino=tipo_destino, destino=destino, rut_destinatario=rut_destinatario,
        pais_destino=pais_destino, tipo_documento=tipo_documento, numero_documento=numero_documento,
        fecha_documento=fecha_documento, observaciones=observaciones))
    despacho = Despacho(fecha_hora_despacho=ahora, registrado_por=usuario, **datos)
    despacho.full_clean()
    try:
        with transaction.atomic():
            despacho.save()
            for estancia in estancias:
                estancia.fecha_hora_salida = ahora
                estancia.retirado_por = usuario
            EstanciaCaja.objects.bulk_update(estancias, ["fecha_hora_salida", "retirado_por"])
            DetalleDespacho.objects.bulk_create([DetalleDespacho(despacho=despacho, caja=c) for c in bloqueadas])
    except IntegrityError as error:
        raise ValidationError("Una o más cajas ya no están disponibles para despacho.") from error
    return despacho


@transaction.atomic
def registrar_baja(*, caja, tipo, motivo, usuario):
    if not tiene_rol(usuario, ROL_JEFE, ROL_ENCARGADA, ROL_OPERARIA):
        raise PermissionDenied
    actual = Caja.objects.select_for_update().filter(pk=caja.pk).first()
    if actual is None:
        raise ValidationError("La caja ya no está disponible para esta operación.")
    if hasattr(actual, "baja"):
        raise ValidationError("La caja ya posee una baja registrada.")
    if hasattr(actual, "detalle_despacho"):
        raise ValidationError("La caja ya fue despachada y no puede darse de baja.")
    situacion = cajas_con_inventario().get(pk=actual.pk)
    if not situacion.puede_registrar_baja:
        raise ValidationError("La caja ya no está disponible para esta operación. Su historial requiere revisión.")
    if tipo not in BajaCaja.Tipo.values:
        raise ValidationError({"tipo": "Seleccione un tipo de baja válido."})
    if not isinstance(motivo, str) or not motivo.strip():
        raise ValidationError({"motivo": "Debe indicar el motivo de la baja."})
    ahora = timezone.now()
    baja = BajaCaja(caja=actual, tipo=tipo, motivo=motivo.strip(), fecha_hora_evento=ahora, registrado_por=usuario)
    baja.full_clean()
    try:
        with transaction.atomic():
            # Sin estancia abierta, este update no crea ni altera historial.
            actual.estancias_inventario.filter(fecha_hora_salida__isnull=True).update(
                fecha_hora_salida=ahora, retirado_por=usuario,
            )
            baja.save()
    except IntegrityError as error:
        raise ValidationError("La caja ya no está disponible para esta operación.") from error
    return baja
