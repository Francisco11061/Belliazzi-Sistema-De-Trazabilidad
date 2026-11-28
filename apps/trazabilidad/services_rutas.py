"""Rutas y operaciones; serialización por especie."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.usuarios.permisos import tiene_rol
from .models import Especie, RutaProceso, EtapaRutaProceso, TipoProceso
from .rutas import validar_etapas_ruta


def exigir_gestion(usuario):
    """Rutas y operaciones las gestionan el jefe y la encargada."""
    if not tiene_rol(usuario, "JEFE", "ENCARGADA"):
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
    exigir_gestion(usuario)
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
    if not ruta and RutaProceso.objects.filter(especie=especie, nombre__iexact=nombre.strip()).exists():
        raise ValidationError(f"Ya hay una ruta llamada «{nombre.strip()}» para {especie.nombre}. Usa otro nombre.")
    ruta = ruta or RutaProceso(creado_por=usuario)
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
    exigir_gestion(usuario)
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


def nombre_para_secuencia(especie, ids):
    """«Eviscerado y glaseado»; si ya existe para la especie, agrega (2), (3)…"""
    nombres = dict(TipoProceso.objects.filter(pk__in=ids).values_list("pk", "nombre"))
    partes = [nombres[pk] for pk in ids]
    base = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]
    base = (base[:1].upper() + base[1:].lower())[:110]
    nombre, n = base, 2
    while RutaProceso.objects.filter(especie=especie, nombre__iexact=nombre).exists():
        nombre, n = f"{base} ({n})", n + 1
    return nombre


@transaction.atomic
def guardar_operacion(*, usuario, codigo, nombre, activo, operacion=None):
    exigir_gestion(usuario)
    if operacion:
        operacion = TipoProceso.objects.select_for_update().get(pk=operacion.pk)
    if codigo == "PROCESAMIENTO" or (operacion and operacion.codigo == "PROCESAMIENTO"):
        raise ValidationError("PROCESAMIENTO está reservado al evento general.")
    operacion = operacion or TipoProceso()
    operacion.codigo, operacion.nombre, operacion.activo = codigo.strip(), nombre.strip(), activo
    operacion.full_clean()
    operacion.save()
    return operacion
