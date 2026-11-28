"""Avisos de la campana: lo que necesita atención ahora. Solo lectura, sin tablas propias.

Los avisos de estado (frío, vencimientos, revisiones, solicitudes por aprobar) desaparecen solos
cuando se resuelven. La respuesta a una solicitud de ruta se quita cuando quien la pidió entra a
«Solicitudes de rutas».
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from apps.inventario.selectors import cajas_con_inventario, totales_stock
from apps.trazabilidad.formato import hace
from apps.trazabilidad.lotes import presentar_codigo_lote
from apps.trazabilidad.models import SolicitudRuta
from apps.trazabilidad.selectors import partidas_con_disponibilidad
from apps.usuarios.permisos import tiene_rol

from .selectors import alertas_frio

POR_TIPO = 5
URGENTE, ATENCION, LISTO, INFO = "urgente", "atencion", "listo", "info"


def _aviso(nivel, titulo, url, detalle=""):
    return dict(nivel=nivel, titulo=titulo, detalle=detalle, url=url)


def _con_resto(avisos, total, url):
    """Corta en POR_TIPO y deja un enlace a la lista completa con lo que falta."""
    if total > len(avisos):
        resto = total - len(avisos)
        avisos.append(dict(nivel="mas", titulo=f"Ver {resto} más", url=url, resto=resto))
    return avisos


def _frio(ahora, jefe):
    frio = alertas_frio(ahora, limite=POR_TIPO)
    avisos = [_aviso(URGENTE, f"{a['especie']}: pasó el tiempo recomendado", a["url"],
                     f"Lleva {a['tiempo']} en {a['unidad'].nombre} · {a['titulo']}") for a in frio["elementos"]]
    resto = reverse("reportes:dashboard") + "#atencion" if jefe else reverse("usuarios:inicio")
    return _con_resto(avisos, frio["total"], resto), frio["total"]


def _vencimientos(jefe):
    hoy = timezone.localdate()
    ventana = max(1, min(365, int(getattr(settings, "DASHBOARD_VENCIMIENTOS_DIAS", 30))))
    filas = list(cajas_con_inventario().filter(
        estado_inventario="ALMACENADA", lote_produccion__fecha_vencimiento__lte=hoy + timedelta(days=ventana),
    ).order_by().values(
        "lote_produccion_id", "lote_produccion__codigo_lote", "lote_produccion__especie__nombre",
        "lote_produccion__fecha_vencimiento",
    ).annotate(**totales_stock()).order_by("lote_produccion__fecha_vencimiento", "lote_produccion_id"))
    avisos = []
    for fila in filas[:POR_TIPO]:
        fecha = fila["lote_produccion__fecha_vencimiento"]
        cuando = ("venció el " if fecha < hoy else "vence hoy" if fecha == hoy else "vence el ")
        cuando += f"{fecha:%d/%m}" if fecha != hoy else ""
        cajas = f"{fila['cajas']} {'caja' if fila['cajas'] == 1 else 'cajas'} en cámara"
        url = reverse("inventario:lista") + f"?estado=ALMACENADA&lote={fila['lote_produccion_id']}"
        avisos.append(_aviso(URGENTE if fecha < hoy else ATENCION,
                             f"{fila['lote_produccion__especie__nombre']}: {cuando}", url,
                             f"Lote {presentar_codigo_lote(fila['lote_produccion__codigo_lote'])} · {cajas}"))
    urgentes = sum(1 for f in filas if f["lote_produccion__fecha_vencimiento"] < hoy)
    resto = reverse("reportes:dashboard") + "#vencimientos" if jefe else reverse("inventario:lista") + "?estado=ALMACENADA"
    return _con_resto(avisos, len(filas), resto), urgentes


def _solicitudes_por_aprobar(ahora, total=None):
    consulta = SolicitudRuta.objects.filter(estado=SolicitudRuta.Estado.PENDIENTE)
    total = consulta.count() if total is None else total
    if not total:
        return []
    avisos = [_aviso(ATENCION, f"{s.solicitante.get_full_name() or s.solicitante.username} pidió una ruta para {s.especie.nombre}",
                     reverse("trazabilidad:resolver_solicitud", args=[s.pk]), f"Enviada {hace(s.creado_en, ahora)}")
              for s in consulta.select_related("solicitante", "especie").order_by("creado_en", "pk")[:POR_TIPO]]
    return _con_resto(avisos, total, reverse("trazabilidad:solicitudes_ruta") + "?estado=PENDIENTE")


def _respuestas(usuario, ahora):
    consulta = SolicitudRuta.objects.filter(solicitante=usuario, respuesta_vista_en__isnull=True).exclude(
        estado=SolicitudRuta.Estado.PENDIENTE).exclude(resuelto_por=F("solicitante"))
    url = reverse("trazabilidad:solicitudes_ruta")
    solicitudes = list(consulta.select_related("especie").order_by("-resuelto_en", "-pk")[:POR_TIPO])
    avisos = []
    for s in solicitudes:
        aprobada = s.estado == SolicitudRuta.Estado.APROBADA
        titulo = f"{'Aprobaron' if aprobada else 'Rechazaron'} tu ruta para {s.especie.nombre}"
        avisos.append(_aviso(LISTO if aprobada else INFO, titulo, url, s.respuesta or hace(s.resuelto_en, ahora).capitalize()))
    return _con_resto(avisos, consulta.count() if len(solicitudes) == POR_TIPO else len(solicitudes), url)


def _historial_por_revisar(total):
    if not total:
        return []
    consulta = partidas_con_disponibilidad().filter(estado="ACTIVA", tiene_hijas=False, situacion="NO_OPERABLE").select_related(
        "detalle_recepcion__especie", "detalle_recepcion__recepcion", "detalle_recepcion__origen_sernapesca")
    avisos = [_aviso(ATENCION, f"{p.especie_nombre}: revisar historial", reverse("trazabilidad:detalle_partida", args=[p.pk]),
                     p.procedencia) for p in consulta.order_by("pk")[:POR_TIPO]]
    return _con_resto(avisos, total, reverse("trazabilidad:lista_partidas") + "?categoria=todos&situacion=NO_OPERABLE")


def _ubicacion_por_revisar(total):
    if not total:
        return []
    cajas = cajas_con_inventario().filter(estado_inventario="REVISION").select_related("lote_produccion__especie")
    avisos = [_aviso(ATENCION, f"Caja #{c.pk}: revisar ubicación", reverse("producto_terminado:detalle_caja", args=[c.pk]),
                     c.lote_produccion.especie.nombre) for c in cajas.order_by("pk")[:POR_TIPO]]
    return _con_resto(avisos, total, reverse("inventario:lista") + "?estado=REVISION")


def avisos_para(usuario, conteos=None, ahora=None):
    """Avisos según el rol. `conteos` trae lo que el menú lateral ya contó, para no repetir consultas."""
    ahora = ahora or timezone.now()
    jefe = tiene_rol(usuario, "JEFE")
    gestion = tiene_rol(usuario, "JEFE", "ENCARGADA")
    conteos = conteos or {}
    avisos, urgentes = [], 0
    if gestion:
        frio, total_frio = _frio(ahora, jefe)
        vencimientos, vencidos = _vencimientos(jefe)
        avisos += frio + vencimientos
        urgentes += total_frio + vencidos
    if jefe:
        avisos += _solicitudes_por_aprobar(ahora, conteos.get("solicitudes"))
    if gestion:
        avisos += _historial_por_revisar(conteos.get("historial", 0))
        avisos += _ubicacion_por_revisar(conteos.get("ubicacion", 0))
    avisos += _respuestas(usuario, ahora)
    # El número cuenta todos los avisos, también los que quedan detrás de «Ver N más».
    total = sum(a.get("resto", 1) for a in avisos)
    return dict(avisos=avisos, total=total, urgente=bool(urgentes))
