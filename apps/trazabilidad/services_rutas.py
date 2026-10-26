"""Altas y aprobación de configuraciones; serialización por especie."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.usuarios.permisos import tiene_rol
from .models import Especie, RutaProceso, EtapaRutaProceso, TipoProceso, SolicitudRuta, EtapaSolicitudRuta
from .rutas import validar_etapas_ruta


def exigir_jefe(usuario):
    if not tiene_rol(usuario, "JEFE"):
        raise PermissionDenied


def secuencia_validada(ids, *, bloquear=False):
    if not ids or len(ids) > 100:
        raise ValidationError("Configura entre 1 y 100 operaciones.")
    consulta = TipoProceso.objects.filter(pk__in=ids).order_by("pk")
    if bloquear:
        consulta = consulta.select_for_update()
    tipos = {t.pk: t for t in consulta}
    if any(pk not in tipos for pk in ids):
        raise ValidationError("Una operación ya no existe.")
    etapas = [EtapaRutaProceso(orden=i, tipo_proceso=tipos[pk]) for i, pk in enumerate(ids, 1)]
    validar_etapas_ruta(etapas)
    return etapas


def rutas_equivalentes(especie, ids, *, excluir=None):
    rutas = RutaProceso.objects.filter(especie=especie, activo=True).exclude(pk=excluir).prefetch_related("etapas__tipo_proceso").order_by("pk")
    return [r for r in rutas if [e.tipo_proceso_id for e in r.etapas.all()] == ids
            and all(e.tipo_proceso.activo for e in r.etapas.all())]


@transaction.atomic
def guardar_ruta(*, usuario, especie, nombre, descripcion, activo, predeterminada, operaciones, ruta=None):
    exigir_jefe(usuario)
    especies = {especie.pk}
    if ruta:
        especies.add(RutaProceso.objects.get(pk=ruta.pk).especie_id)
    list(Especie.objects.select_for_update().filter(pk__in=especies).order_by("pk"))
    if ruta:
        ruta = RutaProceso.objects.select_for_update().get(pk=ruta.pk)
        if ruta.tiene_historial():
            raise ValidationError("Esta ruta ya fue utilizada. Duplícala para cambiar su configuración.")
    etapas = secuencia_validada(operaciones, bloquear=True)
    if activo and rutas_equivalentes(especie, operaciones, excluir=ruta.pk if ruta else None):
        raise ValidationError("Ya existe una ruta activa con esta especie y secuencia. Utiliza esa ruta o desactívala antes de reemplazarla.")
    if predeterminada and not activo:
        raise ValidationError("Una ruta predeterminada debe estar activa.")
    ruta = ruta or RutaProceso()
    ruta.especie, ruta.nombre, ruta.descripcion = especie, nombre.strip(), descripcion.strip()
    ruta.activo, ruta.predeterminada = activo, predeterminada
    ruta.full_clean()
    ruta.save()
    if predeterminada:
        RutaProceso.objects.filter(especie=especie).exclude(pk=ruta.pk).update(predeterminada=False)
    ruta.etapas.all().delete()  # Solo configuración sin historial, comprobada bajo bloqueo.
    for etapa in etapas:
        etapa.ruta = ruta
        etapa.save()
    return ruta


@transaction.atomic
def estado_ruta(*, usuario, ruta, activo, predeterminada=False):
    exigir_jefe(usuario)
    Especie.objects.select_for_update().get(pk=ruta.especie_id)
    ruta = RutaProceso.objects.select_for_update().get(pk=ruta.pk)
    if activo:
        ids = list(ruta.etapas.order_by("orden").values_list("tipo_proceso_id", flat=True))
        secuencia_validada(ids, bloquear=True)
        if rutas_equivalentes(ruta.especie, ids, excluir=ruta.pk):
            raise ValidationError("Ya existe otra ruta activa equivalente.")
    ruta.activo, ruta.predeterminada = activo, predeterminada and activo
    ruta.save(update_fields=["activo", "predeterminada"])
    if ruta.predeterminada:
        RutaProceso.objects.filter(especie_id=ruta.especie_id).exclude(pk=ruta.pk).update(predeterminada=False)
    return ruta


@transaction.atomic
def guardar_operacion(*, usuario, codigo, nombre, activo, operacion=None):
    exigir_jefe(usuario)
    if operacion:
        operacion = TipoProceso.objects.select_for_update().get(pk=operacion.pk)
    if codigo == "PROCESAMIENTO" or (operacion and operacion.codigo == "PROCESAMIENTO"):
        raise ValidationError("PROCESAMIENTO está reservado al evento general.")
    operacion = operacion or TipoProceso()
    operacion.codigo, operacion.nombre, operacion.activo = codigo.strip(), nombre.strip(), activo
    operacion.full_clean()
    operacion.save()
    return operacion


@transaction.atomic
def solicitar_ruta(*, usuario, especie, operaciones, observacion=""):
    if not tiene_rol(usuario, "JEFE", "ENCARGADA", "OPERARIA"):
        raise PermissionDenied
    Especie.objects.select_for_update().get(pk=especie.pk)
    etapas = secuencia_validada(operaciones, bloquear=True)
    if rutas_equivalentes(especie, operaciones):
        raise ValidationError("Ya hay rutas autorizadas con esta secuencia. Selecciona una de ellas.")
    for previa in SolicitudRuta.objects.filter(especie=especie, solicitante=usuario, estado="PENDIENTE").prefetch_related("etapas"):
        if [e.tipo_proceso_id for e in previa.etapas.all()] == operaciones:
            return previa
    solicitud = SolicitudRuta.objects.create(especie=especie, solicitante=usuario, observacion=observacion.strip())
    EtapaSolicitudRuta.objects.bulk_create([EtapaSolicitudRuta(solicitud=solicitud, orden=e.orden, tipo_proceso=e.tipo_proceso) for e in etapas])
    return solicitud


@transaction.atomic
def resolver_solicitud(*, usuario, solicitud, aprobar, nombre="", respuesta="", ruta_existente=None):
    exigir_jefe(usuario)
    Especie.objects.select_for_update().get(pk=solicitud.especie_id)
    solicitud = SolicitudRuta.objects.select_for_update().get(pk=solicitud.pk)
    if solicitud.estado != "PENDIENTE":
        raise ValidationError("La solicitud ya fue resuelta.")
    if aprobar:
        ids = list(solicitud.etapas.values_list("tipo_proceso_id", flat=True))
        secuencia_validada(ids, bloquear=True)
        equivalentes = rutas_equivalentes(solicitud.especie, ids)
        if ruta_existente:
            ruta = next((r for r in equivalentes if r.pk == ruta_existente.pk), None)
            if ruta is None:
                raise ValidationError("La ruta seleccionada no es una coincidencia activa de la solicitud.")
        elif len(equivalentes) == 1:
            ruta = equivalentes[0]
        elif equivalentes:
            raise ValidationError("Hay varias rutas equivalentes. Selecciona explícitamente una.")
        else:
            ruta = guardar_ruta(usuario=usuario, especie=solicitud.especie, nombre=nombre,
                descripcion=solicitud.observacion, activo=True, predeterminada=False, operaciones=ids)
        solicitud.ruta_aprobada = ruta
    solicitud.estado = "APROBADA" if aprobar else "RECHAZADA"
    solicitud.resuelto_por, solicitud.resuelto_en = usuario, timezone.now()
    solicitud.respuesta = respuesta.strip()
    solicitud.save(update_fields=["estado", "ruta_aprobada", "resuelto_por", "resuelto_en", "respuesta"])
    return solicitud
