"""Lecturas compartidas de situación y uso de las partidas, sin escribir datos."""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Case, CharField, Count, DecimalField, Exists, F, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce

from .models import (
    ConsumoLote, EstanciaPartida, EventoProceso, MermaProceso,
    PartidaProceso, Pesaje, UnidadFrio, LoteProduccion, Caja, ComposicionCaja,
)

SITUACIONES = (
    ("DISPONIBLE", "Disponible"),
    ("EN_MANTENCION", "En cámara de mantención"),
    ("EN_PROCESO", "En proceso"),
    ("EN_CONGELACION", "En congelación"),
    ("PENDIENTE_PESAJE", "Pendiente de pesaje"),
    ("LISTA_PARA_EMPAQUE", "Lista para empaque"),
)

SITUACIONES_MERMA = {"EN_PROCESO", "EN_CONGELACION", "PENDIENTE_PESAJE"}
ADVERTENCIA_PESO_SUPERIOR = (
    "El peso postproceso supera la cantidad registrada al inicio de este seguimiento. "
    "Verifique que el valor ingresado sea correcto."
)


def partidas_con_situacion():
    abiertas = EstanciaPartida.objects.filter(
        partida_id=OuterRef("pk"), fecha_hora_salida__isnull=True,
    )
    mantencion = abiertas.filter(unidad_frio__tipo=UnidadFrio.TipoUnidad.MANTENCION)
    tunel = abiertas.filter(unidad_frio__tipo=UnidadFrio.TipoUnidad.TUNEL_CONGELADO)
    generales = EventoProceso.objects.filter(
        partida_id=OuterRef("pk"), tipo_proceso__codigo="PROCESAMIENTO", etapa_ruta__isnull=True,
    )
    return PartidaProceso.objects.select_related(
        "detalle_recepcion__especie", "detalle_recepcion__recepcion",
        "detalle_recepcion__origen_sernapesca", "creado_por", "ruta_proceso",
    ).annotate(
        tiene_hijas=Exists(PartidaProceso.objects.filter(partida_padre_id=OuterRef("pk"))),
        tiene_postproceso=Exists(Pesaje.objects.filter(partida_id=OuterRef("pk"), tipo=Pesaje.POSTPROCESO)),
        tiene_estancia=Exists(abiertas),
        en_mantencion=Exists(mantencion),
        en_congelacion=Exists(tunel),
        proceso_abierto=Exists(generales.filter(fecha_hora_termino__isnull=True)),
        proceso_completado=Exists(generales.filter(fecha_hora_termino__isnull=False)),
        tunel_completado=Exists(EstanciaPartida.objects.filter(
            partida_id=OuterRef("pk"), unidad_frio__tipo=UnidadFrio.TipoUnidad.TUNEL_CONGELADO,
            fecha_hora_salida__isnull=False,
        )),
        tiene_consumos=Exists(ConsumoLote.objects.filter(partida_id=OuterRef("pk"))),
        procesamiento_iniciado=Exists(EventoProceso.objects.filter(partida_id=OuterRef("pk"))),
        ubicacion=Subquery(abiertas.order_by("pk").values("unidad_frio__nombre")[:1]),
        ingreso_actual=Subquery(abiertas.order_by("pk").values("fecha_hora_ingreso")[:1]),
    ).annotate(situacion=Case(
        When(estado=PartidaProceso.Estado.DIVIDIDA, then=Value("DIVIDIDA")),
        When(estado=PartidaProceso.Estado.CONSUMIDA, then=Value("CONSUMIDA")),
        When(estado=PartidaProceso.Estado.CERRADA, then=Value("CERRADA")),
        When(tiene_hijas=True, then=Value("NO_OPERABLE")),
        When(en_mantencion=True, then=Value("EN_MANTENCION")),
        When(en_congelacion=True, then=Value("EN_CONGELACION")),
        When(tiene_estancia=True, then=Value("NO_OPERABLE")),
        When(proceso_abierto=True, then=Value("EN_PROCESO")),
        When(proceso_completado=True, tunel_completado=True, tiene_postproceso=True,
             then=Value("LISTA_PARA_EMPAQUE")),
        When(proceso_completado=True, tunel_completado=True, tiene_consumos=False, tiene_postproceso=False,
             then=Value("PENDIENTE_PESAJE")),
        # No devolver kilos históricos usados a Disponible ante registros incompletos.
        When(procesamiento_iniciado=True, then=Value("NO_OPERABLE")),
        When(ruta_proceso__isnull=False, then=Value("NO_OPERABLE")),
        default=Value("DISPONIBLE"), output_field=CharField(),
    ))


def presentar_partida(partida):
    etiquetas = dict(SITUACIONES) | dict(PartidaProceso.Estado.choices)
    partida.situacion_visible = etiquetas.get(partida.situacion, "No operable")
    partida.puede_registrar_merma = partida.situacion in SITUACIONES_MERMA and not partida.tiene_postproceso
    return partida


def partidas_con_disponibilidad():
    """Peso final menos aportes a lotes; subconsultas separadas evitan multiplicar sumas."""
    decimal = DecimalField(max_digits=20, decimal_places=2)
    postprocesos = Pesaje.objects.filter(partida_id=OuterRef("pk"), tipo=Pesaje.POSTPROCESO)
    consumos = ConsumoLote.objects.filter(partida_id=OuterRef("pk")).order_by().values("partida_id")
    return partidas_con_situacion().annotate(
        peso_final_kg=Subquery(postprocesos.order_by("pk").values("peso_kg")[:1], output_field=decimal),
        numero_postprocesos=Subquery(postprocesos.order_by().values("partida_id").annotate(
            n=Count("pk"),
        ).values("n")),
        asignado_lotes_kg=Coalesce(Subquery(consumos.annotate(
            total=Sum("cantidad_kg_utilizada"),
        ).values("total"), output_field=decimal), Value(Decimal("0.00")), output_field=decimal),
    ).annotate(disponible_lote_kg=F("peso_final_kg") - F("asignado_lotes_kg"))


def partidas_para_lote(lote=None):
    partidas = partidas_con_disponibilidad().filter(
        situacion="LISTA_PARA_EMPAQUE", numero_postprocesos=1,
        disponible_lote_kg__gt=0, ruta_proceso__isnull=False,
    )
    if lote is not None:
        if not lote.ruta_proceso_id:
            return partidas.none()
        partidas = partidas.filter(
            detalle_recepcion__especie_id=lote.especie_id, ruta_proceso_id=lote.ruta_proceso_id,
        ).exclude(consumos_lote__lote_produccion=lote)
    return partidas.order_by("pk")


def puede_asignar_lote(partida):
    return (partida.situacion == "LISTA_PARA_EMPAQUE" and partida.ruta_proceso_id is not None
            and partida.numero_postprocesos == 1 and partida.disponible_lote_kg > 0)


def lotes_con_totales():
    return LoteProduccion.objects.select_related("especie", "ruta_proceso", "registrado_por").annotate(
        total_asignado=Sum("consumos__cantidad_kg_utilizada", default=Decimal("0.00")),
        numero_fuentes=Count("consumos"),
    )


def cajas_con_peso():
    return Caja.objects.select_related(
        "lote_produccion__especie", "lote_produccion__ruta_proceso", "registrado_por",
    ).annotate(peso_teorico_kg=Sum(
        F("composiciones__cantidad") * F("composiciones__peso_unitario_kg"),
        output_field=DecimalField(max_digits=30, decimal_places=2),
    )).annotate(diferencia_kg=F("peso_neto_kg") - F("peso_teorico_kg"))


def lotes_con_empaque():
    decimal = DecimalField(max_digits=30, decimal_places=2)
    cajas = Caja.objects.filter(lote_produccion_id=OuterRef("pk"))
    return lotes_con_totales().annotate(
        peso_real_documentado=Coalesce(Subquery(cajas.order_by().values("lote_produccion_id").annotate(
            total=Sum("peso_neto_kg", output_field=decimal),
        ).values("total"), output_field=decimal), Value(Decimal("0.00")), output_field=decimal),
        numero_cajas=Coalesce(Subquery(cajas.order_by().values("lote_produccion_id").annotate(
            n=Count("pk"),
        ).values("n")), Value(0)),
        historial_empaque_incompleto=Exists(cajas.filter(peso_neto_kg__isnull=True)),
    ).annotate(total_empacado=Case(
        When(historial_empaque_incompleto=True, then=Value(None)),
        default=F("peso_real_documentado"), output_field=decimal,
    )).annotate(disponible_empacar=F("total_asignado") - F("total_empacado"))


def puede_crear_caja(lote):
    return bool(lote.ruta_proceso_id and not lote.historial_empaque_incompleto and lote.disponible_empacar > 0)


def resumen_pesajes_mermas(partida):
    """Historial completo y cálculos de presentación, sin campos derivados persistidos."""
    pesajes = list(partida.pesajes.select_related("registrado_por").order_by("fecha_hora_evento", "pk"))
    mermas = list(partida.mermas.select_related(
        "registrado_por", "evento_proceso__tipo_proceso",
    ).order_by("fecha_hora_evento", "pk"))
    postproceso = next((p for p in pesajes if p.tipo == Pesaje.POSTPROCESO), None)
    cantidad = partida.cantidad_inicial_kg
    return {
        "pesajes": pesajes, "mermas": mermas, "peso_postproceso": postproceso,
        "total_mermas": sum((m.cantidad_kg for m in mermas), Decimal("0.00")),
        "diferencia": cantidad - postproceso.peso_kg if postproceso else None,
        "rendimiento": (postproceso.peso_kg / cantidad * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        ) if postproceso and cantidad > 0 else None,
        "advertencia_peso": ADVERTENCIA_PESO_SUPERIOR if postproceso and postproceso.peso_kg > cantidad else "",
    }


def partidas_con_uso():
    """Una raíz intacta no tiene ninguna evidencia de uso, incluso ya finalizado."""
    return PartidaProceso.objects.annotate(
        uso_hijas=Exists(PartidaProceso.objects.filter(partida_padre_id=OuterRef("pk"))),
        uso_frio=Exists(EstanciaPartida.objects.filter(partida_id=OuterRef("pk"))),
        uso_eventos=Exists(EventoProceso.objects.filter(partida_id=OuterRef("pk"))),
        uso_pesajes=Exists(Pesaje.objects.filter(partida_id=OuterRef("pk"))),
        uso_mermas=Exists(MermaProceso.objects.filter(partida_id=OuterRef("pk"))),
        uso_consumos=Exists(ConsumoLote.objects.filter(partida_id=OuterRef("pk"))),
    ).exclude(
        estado=PartidaProceso.Estado.ACTIVA, partida_padre__isnull=True, ruta_proceso__isnull=True,
        uso_hijas=False, uso_frio=False, uso_eventos=False, uso_pesajes=False,
        uso_mermas=False, uso_consumos=False,
    )


def recepcion_con_actividad(recepcion):
    if partidas_con_uso().filter(detalle_recepcion__recepcion=recepcion).exists():
        return True
    # Datos históricos ambiguos: varias raíces no representan un único peso derivado.
    return PartidaProceso.objects.filter(detalle_recepcion__recepcion=recepcion).values(
        "detalle_recepcion_id",
    ).annotate(total=Count("pk")).filter(total__gt=1).exists()


def resumen_procesamiento(partida):
    """Una consulta para eventos y otra para etapas; ninguna por fila."""
    eventos = list(partida.eventos.select_related(
        "tipo_proceso", "iniciado_por", "finalizado_por",
    ).order_by("fecha_hora_inicio", "pk"))
    general = next((e for e in eventos if e.tipo_proceso.codigo == "PROCESAMIENTO" and not e.etapa_ruta_id), None)
    etapas = list(partida.ruta_proceso.etapas.select_related("tipo_proceso").order_by("orden")) if partida.ruta_proceso_id else []
    por_etapa = {e.etapa_ruta_id: e for e in eventos if e.etapa_ruta_id}
    anteriores_completas = True
    abierta = any(e.etapa_ruta_id and e.fecha_hora_termino is None for e in eventos)
    filas = []
    for etapa in etapas:
        evento = por_etapa.get(etapa.pk)
        completa = evento is not None and evento.fecha_hora_termino is not None
        filas.append({
            "etapa": etapa, "evento": evento,
            "estado": "COMPLETADA" if completa else "EN CURSO" if evento else "PENDIENTE",
            "puede_iniciar": partida.situacion == "EN_PROCESO" and anteriores_completas and not abierta and evento is None,
            "puede_finalizar": partida.situacion == "EN_PROCESO" and evento is not None and not completa,
        })
        anteriores_completas = anteriores_completas and completa
    completa = bool(etapas) and anteriores_completas and etapas[-1].tipo_proceso.codigo == "EMPARRILLADO"
    return {
        "general": general, "etapas_proceso": filas, "ruta_completa": completa,
        "puede_enviar_tunel": completa and partida.situacion == "EN_PROCESO",
        "puede_asignar_ruta": partida.situacion == "EN_PROCESO" and not partida.ruta_proceso_id
        and not any(e.etapa_ruta_id or e.tipo_proceso.codigo != "PROCESAMIENTO" for e in eventos),
    }
