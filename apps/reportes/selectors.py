"""Lecturas del dashboard. Inventario continúa siendo la fuente del stock."""
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.db.models import Case, Count, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja
from apps.inventario.selectors import cajas_con_inventario, despachos_con_totales, resumen_inventario, totales_stock
from apps.trazabilidad.models import (Caja, ConsumoLote, DetalleRecepcion, EstanciaPartida,
                                     LoteProduccion, MermaProceso, Pesaje, Recepcion, UnidadFrio)
from apps.trazabilidad.selectors import lotes_con_empaque

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


def _rango_reporte(queryset, campo, inicio, fin, fecha_sin_hora=False):
    if fecha_sin_hora:
        queryset = queryset.filter(**{campo + "__lte": fin})
        return queryset.filter(**{campo + "__gte": inicio}) if inicio else queryset
    queryset = queryset.filter(**{campo + "__lt": _medianoche(fin + timedelta(days=1))})
    return queryset.filter(**{campo + "__gte": _medianoche(inicio)}) if inicio else queryset


def _abastecimiento_reporte(inicio, fin):
    return _rango_reporte(DetalleRecepcion.objects.select_related(
        "recepcion__registrado_por", "origen_sernapesca", "especie"
    ), "recepcion__fecha_hora_recepcion", inicio, fin).order_by("recepcion__fecha_hora_recepcion", "recepcion_id", "pk")


def _produccion_reporte(inicio, fin):
    return _rango_reporte(lotes_con_empaque(), "fecha_elaboracion", inicio, fin, True).order_by("fecha_elaboracion", "pk")


def _destino_reporte(inicio, fin):
    return _rango_reporte(DetalleDespacho.objects.select_related(
        "despacho__registrado_por", "caja__lote_produccion__especie"
    ), "despacho__fecha_hora_despacho", inicio, fin).order_by("despacho__fecha_hora_despacho", "despacho_id", "caja_id")


def resumen_reporte_sernapesca(inicio, fin):
    return dict(abastecimiento=_abastecimiento_reporte(inicio, fin).count(),
                produccion=_produccion_reporte(inicio, fin).count(),
                destino=_destino_reporte(inicio, fin).count())


def _usuario_reporte(usuario):
    nombre = usuario.get_full_name()
    return f"{usuario.username} ({nombre})" if nombre else usuario.username


def _aportes_reporte(referencias=False):
    aportes = ConsumoLote.objects.select_related("partida__detalle_recepcion__origen_sernapesca").order_by("partida_id")
    if referencias:
        aportes = aportes.prefetch_related(
            Prefetch("partida__pesajes", queryset=Pesaje.objects.filter(tipo=Pesaje.POSTPROCESO), to_attr="pesajes_reporte"),
            Prefetch("partida__mermas", queryset=MermaProceso.objects.all(), to_attr="mermas_reporte"),
        )
    return aportes


def _folios_reporte(aportes):
    origenes = {a.partida.detalle_recepcion.origen_sernapesca_id:
                a.partida.detalle_recepcion.origen_sernapesca.folio_origen for a in aportes}
    if not origenes:
        return ""
    if len(origenes) == 1:
        return next(iter(origenes.values()))
    return "Múltiples orígenes históricos: " + "; ".join(
        f"Origen #{pk}: {folio}" for pk, folio in sorted(origenes.items())
    )


def obtener_abastecimiento_reporte(inicio, fin):
    filas = []
    for detalle in _abastecimiento_reporte(inicio, fin):
        recepcion, origen = detalle.recepcion, detalle.origen_sernapesca
        filas.append(dict(recepcion=recepcion.pk, fecha=recepcion.fecha_hora_recepcion, folio=origen.folio_origen,
                          tipo_origen=origen.tipo_origen, agente=origen.codigo_agente, proveedor=origen.proveedor,
                          especie=detalle.especie.nombre, peso_origen=detalle.peso_origen_kg,
                          peso_recibido=detalle.peso_recepcion_kg,
                          diferencia=detalle.peso_recepcion_kg - detalle.peso_origen_kg,
                          usuario=_usuario_reporte(recepcion.registrado_por), observaciones=recepcion.observaciones))
    return filas


def obtener_produccion_reporte(inicio, fin):
    sin_peso = Caja.objects.filter(lote_produccion_id=OuterRef("pk"), peso_neto_kg__isnull=True).order_by().values(
        "lote_produccion_id").annotate(n=Count("pk")).values("n")
    lotes = _produccion_reporte(inicio, fin).annotate(cajas_sin_peso=Coalesce(Subquery(sin_peso), Value(0))).prefetch_related(
        Prefetch("consumos", queryset=_aportes_reporte(referencias=True), to_attr="aportes_reporte")
    )
    filas = []
    for lote in lotes:
        aportes = lote.aportes_reporte
        partidas = {a.partida_id: a.partida for a in aportes}
        pesos = [p.pesajes_reporte[0].peso_kg for p in partidas.values() if len(p.pesajes_reporte) == 1]
        sin_pesaje = [str(pk) for pk, p in partidas.items() if not p.pesajes_reporte]
        ambiguos = [str(pk) for pk, p in partidas.items() if len(p.pesajes_reporte) > 1]
        mermas = {tipo: Decimal("0.00") for tipo in MermaProceso.Tipo.values}
        for partida in partidas.values():
            for merma in partida.mermas_reporte:
                if merma.tipo in mermas:
                    mermas[merma.tipo] += merma.cantidad_kg
        notas = []
        if not partidas:
            notas.append("Sin seguimientos documentados.")
        if sin_pesaje:
            notas.append("Sin peso postproceso: seguimientos " + ", ".join(sin_pesaje) + ".")
        if ambiguos:
            notas.append("Pesajes postproceso ambiguos excluidos: seguimientos " + ", ".join(ambiguos) + ".")
        filas.append(dict(fecha=lote.fecha_elaboracion, lote=lote.codigo_lote, folio=_folios_reporte(aportes),
                          especie=lote.especie.nombre, ruta=lote.ruta_proceso.nombre if lote.ruta_proceso else "",
                          seguimientos=", ".join(str(pk) for pk in partidas),
                          postproceso=sum(pesos, Decimal("0.00")) if pesos else None,
                          asignado=lote.total_asignado, merma=mermas["MERMA"] if partidas else None,
                          descarte=mermas["DESCARTE"] if partidas else None, perdida=mermas["PERDIDA"] if partidas else None,
                          cajas=lote.numero_cajas, empacado=lote.peso_real_documentado, sin_peso=lote.cajas_sin_peso,
                          vencimiento=lote.fecha_vencimiento, usuario=_usuario_reporte(lote.registrado_por),
                          observaciones=lote.observaciones, notas=" ".join(notas)))
    return filas


def obtener_destino_reporte(inicio, fin):
    detalles = _destino_reporte(inicio, fin).prefetch_related(
        Prefetch("caja__lote_produccion__consumos", queryset=_aportes_reporte(), to_attr="aportes_reporte")
    )
    filas = []
    for detalle in detalles:
        despacho, caja = detalle.despacho, detalle.caja
        lote = caja.lote_produccion
        filas.append(dict(despacho=despacho.pk, fecha=despacho.fecha_hora_despacho,
                          tipo_destino=despacho.get_tipo_destino_display(), destinatario=despacho.destino,
                          rut=despacho.rut_destinatario, pais=despacho.pais_destino,
                          tipo_documento=despacho.tipo_documento, numero_documento=despacho.numero_documento,
                          fecha_documento=despacho.fecha_documento, caja=caja.pk, lote=lote.codigo_lote,
                          especie=lote.especie.nombre, peso=caja.peso_neto_kg,
                          folio=_folios_reporte(lote.aportes_reporte), usuario=_usuario_reporte(despacho.registrado_por),
                          observaciones=despacho.observaciones))
    return filas
