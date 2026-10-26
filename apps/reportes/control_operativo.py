"""Consultas de lectura para el centro de control; sin métricas persistidas."""
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import DetalleDespacho
from apps.inventario.selectors import totales_stock
from apps.trazabilidad.models import SolicitudRuta
from apps.trazabilidad.selectors import SITUACIONES, partidas_con_disponibilidad


def enlace(nombre, **parametros):
    valores = {k: v for k, v in parametros.items() if v is not None and v != ""}
    return reverse(nombre) + ("?" + urlencode(valores) if valores else "")


def control_operativo(*, cajas, inventario, periodo, ahora, especie, frio):
    from .selectors import _en_periodo, _especie, _totales_cajas_relacionadas, _duracion_texto
    especie_id = especie.pk if especie else None
    partidas = _especie(partidas_con_disponibilidad(), "detalle_recepcion__especie", especie).filter(
        estado="ACTIVA", tiene_hijas=False)
    cantidades = dict(partidas.order_by().values_list("situacion").annotate(n=Count("pk")))
    produccion = []
    for codigo, nombre in (*SITUACIONES, ("NO_OPERABLE", "Requiere revisión")):
        produccion.append(dict(codigo=codigo, nombre=nombre, cantidad=cantidades.get(codigo, 0),
            url=enlace("trazabilidad:lista_partidas", categoria="todos", situacion=codigo, especie=especie_id)))
    maximo = max((f["cantidad"] for f in produccion), default=0) or 1
    for fila in produccion:
        fila["porcentaje"] = round(fila["cantidad"] / maximo * 100)
    disponibles = partidas.filter(situacion="LISTA_PARA_EMPAQUE")
    empaque = disponibles.aggregate(
        kg=Sum("disponible_lote_kg", filter=Q(numero_postprocesos=1, disponible_lote_kg__gte=0)),
        incompletos=Count("pk", filter=~Q(numero_postprocesos=1) | Q(disponible_lote_kg__lt=0)))
    solicitudes = _especie(SolicitudRuta.objects.filter(estado="PENDIENTE"), "especie", especie).count()
    pendientes = [
        dict(nombre="Por ingresar a almacenamiento", cantidad=inventario["pendientes"],
             url=enlace("inventario:lista", estado="PENDIENTE", especie=especie_id)),
        dict(nombre="Ubicación por revisar", cantidad=inventario["revision"],
             url=enlace("inventario:lista", estado="REVISION", especie=especie_id)),
        dict(nombre="Cajas almacenadas sin peso documentado", cantidad=inventario["stock"]["sin_peso"],
             url=enlace("inventario:lista", estado="ALMACENADA", sin_peso="on", especie=especie_id)),
        dict(nombre="Seguimientos que requieren revisión", cantidad=cantidades.get("NO_OPERABLE", 0),
             url=produccion[-1]["url"]),
        dict(nombre="Solicitudes de rutas pendientes", cantidad=solicitudes,
             url=enlace("trazabilidad:solicitudes_ruta", estado="PENDIENTE", especie=especie_id)),
    ]
    camaras = list(inventario["por_camara"].order_by("-peso_conocido", "nombre_unidad_abierta"))
    for fila in camaras:
        fila["url"] = enlace("inventario:lista", estado="ALMACENADA", camara=fila["unidad_abierta_id"], especie=especie_id)
    ranking = list(_en_periodo(_especie(DetalleDespacho.objects.all(), "caja__lote_produccion__especie", especie),
        "despacho__fecha_hora_despacho", periodo).order_by().values(
            "caja__lote_produccion__especie_id", "caja__lote_produccion__especie__nombre").annotate(
                **_totales_cajas_relacionadas()).order_by("-peso_conocido", "caja__lote_produccion__especie__nombre")[:5])
    for fila in ranking:
        fila["url"] = enlace("inventario:lista_despachos", especie=fila["caja__lote_produccion__especie_id"],
                             desde=periodo["desde"], hasta=periodo["hasta"])
    stock = cajas.filter(estado_inventario="ALMACENADA")
    hoy = timezone.localtime(ahora, timezone.get_default_timezone()).date()
    ventana = max(1, min(365, int(getattr(settings, "DASHBOARD_VENCIMIENTOS_DIAS", 30))))
    limite = hoy + timedelta(days=ventana)
    grupos = stock.aggregate(
        vencidos=Count("lote_produccion_id", distinct=True, filter=Q(lote_produccion__fecha_vencimiento__lt=hoy)),
        proximos=Count("lote_produccion_id", distinct=True, filter=Q(lote_produccion__fecha_vencimiento__range=(hoy, limite))),
        sin_fecha=Count("lote_produccion_id", distinct=True, filter=Q(lote_produccion__fecha_vencimiento__isnull=True)))
    vencimientos = list(stock.order_by().values("lote_produccion_id", "lote_produccion__codigo_lote",
        "lote_produccion__especie__nombre", "lote_produccion__fecha_vencimiento").annotate(**totales_stock()).filter(
            Q(lote_produccion__fecha_vencimiento__lte=limite) | Q(lote_produccion__fecha_vencimiento__isnull=True)).order_by(
                Case(When(lote_produccion__fecha_vencimiento__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField()),
                "lote_produccion__fecha_vencimiento", "lote_produccion_id")[:8])
    for fila in vencimientos:
        fecha = fila["lote_produccion__fecha_vencimiento"]
        fila["grupo"] = "sin_fecha" if fecha is None else "vencidos" if fecha < hoy else "proximos"
        fila["etiqueta"] = dict(vencidos="Vencido", proximos="Próximo a vencer", sin_fecha="Sin fecha documentada")[fila["grupo"]]
        fila["url"] = enlace("inventario:lista", estado="ALMACENADA", lote=fila["lote_produccion_id"], especie=especie_id)
    # La lista completa está en Inventario; limitar la presentación evita una página interminable.
    vencimientos.sort(key=lambda f: (dict(vencidos=0, proximos=1, sin_fecha=2)[f["grupo"]],
                                    f["lote_produccion__fecha_vencimiento"] or limite, f["lote_produccion_id"]))
    antiguos = list(stock.order_by("ingreso_abierto", "pk")[:6])
    for caja in antiguos:
        caja.permanencia = _duracion_texto(ahora - caja.ingreso_abierto) if caja.ingreso_abierto <= ahora else "Ingreso futuro: revisar"
    return dict(produccion=produccion, procesamiento_activo=cantidades.get("EN_PROCESO", 0),
        listos_empaque=cantidades.get("LISTA_PARA_EMPAQUE", 0), saldo_empaque=empaque,
        asuntos=frio["total"] + sum(p["cantidad"] for p in pendientes), pendientes_control=pendientes,
        solicitudes_ruta_pendientes=solicitudes, camaras=camaras, ranking=ranking,
        vencimientos=vencimientos, vencimientos_total=sum(grupos.values()), vencimientos_resumen=grupos,
        ventana_vencimientos=ventana, antiguos=antiguos,
        stock_url=enlace("inventario:lista", estado="ALMACENADA", especie=especie_id),
        despachos_url=enlace("inventario:lista_despachos", especie=especie_id, desde=periodo["desde"], hasta=periodo["hasta"]),
        proceso_url=produccion[2]["url"], empaque_url=produccion[5]["url"])
