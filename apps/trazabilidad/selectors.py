"""Lecturas compartidas de situación y uso de las partidas, sin escribir datos."""
from django.db.models import Case, CharField, Count, Exists, OuterRef, Subquery, Value, When

from .models import (
    ConsumoLote, EstanciaPartida, EventoProceso, MermaProceso,
    PartidaProceso, Pesaje, UnidadFrio,
)

SITUACIONES = (
    ("DISPONIBLE", "Disponible"),
    ("EN_MANTENCION", "En cámara de mantención"),
    ("EN_PROCESO", "En proceso"),
    ("EN_CONGELACION", "En congelación"),
    ("LISTA_PARA_EMPAQUE", "Lista para empaque"),
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
        When(proceso_completado=True, tunel_completado=True, tiene_consumos=False,
             then=Value("LISTA_PARA_EMPAQUE")),
        # No devolver kilos históricos usados a Disponible ante registros incompletos.
        When(procesamiento_iniciado=True, then=Value("NO_OPERABLE")),
        When(ruta_proceso__isnull=False, then=Value("NO_OPERABLE")),
        default=Value("DISPONIBLE"), output_field=CharField(),
    ))


def presentar_partida(partida):
    etiquetas = dict(SITUACIONES) | dict(PartidaProceso.Estado.choices)
    partida.situacion_visible = etiquetas.get(partida.situacion, "No operable")
    return partida


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
