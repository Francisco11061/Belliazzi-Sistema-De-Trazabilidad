"""Lecturas del dashboard. Inventario continúa siendo la fuente del stock."""
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from apps.inventario.selectors import cajas_con_inventario, despachos_con_totales, resumen_inventario, totales_stock
from apps.trazabilidad.models import EstanciaPartida, LoteProduccion, MermaProceso, Recepcion, UnidadFrio

PERIODOS = (("semana", "Semana"), ("mes", "Mes"), ("ano", "Año"))


def cajas_disponibles():
    return cajas_con_inventario().filter(estado_inventario="ALMACENADA")


def resumen_stock():
    """Compatibilidad del selector anterior, ahora basado en peso real y estado oficial."""
    stock = cajas_disponibles().aggregate(**totales_stock())
    return {"total_cajas": stock["cajas"], "peso_total_kg": stock["peso_conocido"], "sin_peso": stock["sin_peso"]}


def _medianoche(dia):
    return timezone.make_aware(datetime.combine(dia, time.min), timezone.get_default_timezone())


def periodo_actual(valor, ahora):
    valor = valor if valor in dict(PERIODOS) else "semana"
    hoy = timezone.localtime(ahora, timezone.get_default_timezone()).date()
    if valor == "semana":
        inicio = hoy - timedelta(days=hoy.weekday())
        limites = [inicio + timedelta(days=n) for n in range(8)]
    elif valor == "mes":
        inicio = hoy.replace(day=1)
        fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1)
        limites = [inicio + timedelta(days=n) for n in range((fin - inicio).days + 1)]
    else:
        limites = [date(hoy.year, mes, 1) for mes in range(1, 13)] + [date(hoy.year + 1, 1, 1)]
    etiquetas_mes = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
    segmentos = [dict(inicio=_medianoche(a), fin=_medianoche(b),
                      etiqueta=etiquetas_mes[a.month - 1] if valor == "ano" else a.strftime("%d/%m"))
                 for a, b in zip(limites, limites[1:])]
    return dict(valor=valor, nombre=dict(PERIODOS)[valor], segmentos=segmentos,
                inicio=segmentos[0]["inicio"], fin=segmentos[-1]["fin"],
                desde=limites[0], hasta=limites[-1] - timedelta(days=1))


def _en_periodo(queryset, campo, periodo):
    return queryset.filter(**{campo + "__gte": periodo["inicio"], campo + "__lt": periodo["fin"]})


def _totales_cajas_relacionadas():
    return dict(cajas=Count("pk"), peso_conocido=Sum("caja__peso_neto_kg", default=Decimal("0.00")),
                sin_peso=Count("pk", filter=Q(caja__peso_neto_kg__isnull=True)))


def despachos_del_dia(ahora):
    hoy = timezone.localtime(ahora, timezone.get_default_timezone()).date()
    periodo = dict(inicio=_medianoche(hoy), fin=_medianoche(hoy + timedelta(days=1)))
    detalles = _en_periodo(DetalleDespacho.objects.all(), "despacho__fecha_hora_despacho", periodo)
    return dict(despachos=_en_periodo(Despacho.objects.all(), "fecha_hora_despacho", periodo).count(),
                **detalles.aggregate(**_totales_cajas_relacionadas()))


def serie_despachos(periodo):
    # Rangos aware: agrupa en SQL sin exigir tablas de zonas horarias a MySQL.
    # Los límites de cada día chileno se convierten por separado, respetando DST.
    campo = "despacho__fecha_hora_despacho"
    grupo = Case(*[When(**{campo + "__gte": s["inicio"], campo + "__lt": s["fin"]}, then=Value(i))
                   for i, s in enumerate(periodo["segmentos"])], output_field=IntegerField())
    filas = _en_periodo(DetalleDespacho.objects.all(), campo, periodo).order_by().annotate(
        grupo=grupo).values("grupo").annotate(**_totales_cajas_relacionadas())
    por_grupo = {fila["grupo"]: fila for fila in filas}
    return [dict(etiqueta=s["etiqueta"], **por_grupo.get(i, dict(cajas=0, peso_conocido=Decimal("0.00"), sin_peso=0)))
            for i, s in enumerate(periodo["segmentos"])]


def mermas_y_bajas(periodo):
    filas = _en_periodo(MermaProceso.objects.all(), "fecha_hora_evento", periodo).order_by().values(
        "tipo").annotate(kg=Sum("cantidad_kg"))
    por_tipo = {fila["tipo"]: fila["kg"] for fila in filas}
    mermas = [dict(tipo=tipo, nombre=nombre, kg=por_tipo.get(tipo, Decimal("0.00")))
              for tipo, nombre in MermaProceso.Tipo.choices]
    bajas = _en_periodo(BajaCaja.objects.all(), "fecha_hora_evento", periodo).aggregate(**_totales_cajas_relacionadas())
    return mermas, bajas


def _duracion_texto(duracion):
    segundos = max(0, int(duracion.total_seconds()))
    if segundos < 60:
        return "Menos de 1 min"
    horas, minutos = divmod(segundos // 60, 60)
    return f"{horas} h {minutos:02d} min"


def alertas_frio(ahora, limite=10):
    ahora = ahora.astimezone(dt_timezone.utc)
    unidades = list(UnidadFrio.objects.filter(umbral_alerta_horas__isnull=False))
    if not unidades:
        return dict(total=0, elementos=[], configuradas=0)
    condicion = Q(pk__in=[])
    for unidad in unidades:
        umbral = timedelta(seconds=int(unidad.umbral_alerta_horas * 3600))
        condicion |= Q(unidad_frio_id=unidad.pk, fecha_hora_ingreso__lt=ahora - umbral)
    # Solo las estancias que superan su umbral llegan desde SQL. Se limita cada
    # fuente antes de combinar, ordenando por ingreso más antiguo, no por especie.
    fuentes = (
        (EstanciaPartida, "partida__detalle_recepcion__especie", "seguimiento"),
        (EstanciaCaja, "caja__lote_produccion__especie", "caja"),
    )
    elementos, total = [], 0
    for modelo, relacion, tipo in fuentes:
        abiertas = modelo.objects.filter(condicion, fecha_hora_salida__isnull=True)
        total += abiertas.count()
        for estancia in abiertas.select_related("unidad_frio", relacion).order_by("fecha_hora_ingreso", "pk")[:limite]:
            unidad = estancia.unidad_frio
            duracion = ahora - estancia.fecha_hora_ingreso.astimezone(dt_timezone.utc)
            exceso = duracion - timedelta(seconds=int(unidad.umbral_alerta_horas * 3600))
            if tipo == "seguimiento":
                especie = estancia.partida.detalle_recepcion.especie.nombre
                titulo = f"Seguimiento #{estancia.partida_id}"
                url = reverse("trazabilidad:detalle_partida", args=[estancia.partida_id])
            else:
                especie = estancia.caja.lote_produccion.especie.nombre
                titulo = f"Caja #{estancia.caja_id}"
                url = reverse("producto_terminado:detalle_caja", args=[estancia.caja_id])
            elementos.append(dict(titulo=titulo, especie=especie, unidad=unidad, url=url,
                                  ingreso=estancia.fecha_hora_ingreso, duracion=duracion, exceso=exceso,
                                  tiempo=_duracion_texto(duracion), exceso_texto=_duracion_texto(exceso)))
    elementos.sort(key=lambda e: (e["ingreso"], e["titulo"]))
    return dict(total=total, elementos=elementos[:limite], configuradas=len(unidades))


def actividad_reciente(limite=8):
    actividades = []
    for recepcion in Recepcion.objects.order_by("-creado_en", "-pk")[:limite]:
        actividades.append(dict(fecha=recepcion.creado_en, titulo=f"Recepción #{recepcion.pk} registrada",
                                url=reverse("trazabilidad:detalle_recepcion", args=[recepcion.pk])))
    for lote in LoteProduccion.objects.order_by("-creado_en", "-pk")[:limite]:
        actividades.append(dict(fecha=lote.creado_en, titulo=f"Lote {lote.codigo_visible} creado",
                                url=reverse("producto_terminado:detalle_lote", args=[lote.pk])))
    for despacho in despachos_con_totales().order_by("-creado_en", "-pk")[:limite]:
        actividades.append(dict(fecha=despacho.creado_en, titulo=f"Despacho #{despacho.pk} registrado",
                                cajas=despacho.cantidad_cajas, kg=despacho.peso_conocido, sin_peso=despacho.cajas_sin_peso,
                                url=reverse("inventario:detalle_despacho", args=[despacho.pk])))
    for baja in BajaCaja.objects.select_related("caja").order_by("-creado_en", "-pk")[:limite]:
        actividades.append(dict(fecha=baja.creado_en, titulo=f"Baja de Caja #{baja.caja_id} registrada",
                                url=reverse("producto_terminado:detalle_caja", args=[baja.caja_id])))
    return sorted(actividades, key=lambda a: (a["fecha"], a["titulo"]), reverse=True)[:limite]


def dashboard_operacional(valor="semana", ahora=None):
    ahora = ahora or timezone.now()
    periodo = periodo_actual(valor, ahora)
    inventario = resumen_inventario(cajas_con_inventario())
    especies = list(inventario["por_especie"].order_by("-peso_conocido", "lote_produccion__especie__nombre"))
    serie = serie_despachos(periodo)
    mermas, bajas = mermas_y_bajas(periodo)
    return dict(actualizado=ahora, periodo=periodo, periodos=PERIODOS,
                stock=inventario["stock"], pendientes=inventario["pendientes"], especies=especies,
                despachos_hoy=despachos_del_dia(ahora), serie=serie,
                cajas_periodo=sum(f["cajas"] for f in serie),
                sin_peso_periodo=sum(f["sin_peso"] for f in serie),
                kg_periodo=sum((f["peso_conocido"] for f in serie), Decimal("0.00")),
                mermas=mermas, bajas=bajas, frio=alertas_frio(ahora), actividad=actividad_reciente(),
                graficos={
                    "despachos": {"labels": [f["etiqueta"] for f in serie], "valores": [f["peso_conocido"] for f in serie]},
                    "especies": {"labels": [f["lote_produccion__especie__nombre"] for f in especies],
                                 "valores": [f["peso_conocido"] for f in especies]},
                })
