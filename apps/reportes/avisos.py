"""Avisos de la campana del jefe y la encargada: lo que necesita atención ahora.

Solo lectura y sin tablas propias: cada aviso desaparece solo cuando se resuelve.
"""
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.inventario.selectors import cajas_con_inventario, totales_stock
from apps.trazabilidad.lotes import presentar_codigo_lote
from apps.trazabilidad.selectors import partidas_con_disponibilidad
from apps.usuarios.permisos import tiene_rol

from .selectors import alertas_frio

POR_TIPO = 5
URGENTE, ATENCION = "urgente", "atencion"


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
    """Avisos del jefe y la encargada. `conteos` trae lo que el menú lateral ya contó, para no repetir consultas."""
    if not tiene_rol(usuario, "JEFE", "ENCARGADA"):
        return dict(avisos=[], total=0, urgente=False)
    ahora = ahora or timezone.now()
    jefe = tiene_rol(usuario, "JEFE")
    conteos = conteos or {}
    frio, total_frio = _frio(ahora, jefe)
    vencimientos, vencidos = _vencimientos(jefe)
    avisos = frio + vencimientos
    avisos += _historial_por_revisar(conteos.get("historial", 0))
    avisos += _ubicacion_por_revisar(conteos.get("ubicacion", 0))
    urgentes = total_frio + vencidos
    # El número cuenta todos los avisos, también los que quedan detrás de «Ver N más».
    total = sum(a.get("resto", 1) for a in avisos)
    return dict(avisos=avisos, total=total, urgente=bool(urgentes))
