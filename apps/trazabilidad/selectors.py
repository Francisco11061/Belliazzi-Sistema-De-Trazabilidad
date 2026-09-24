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
)


def partidas_con_situacion():
    abiertas = EstanciaPartida.objects.filter(
        partida_id=OuterRef("pk"), fecha_hora_salida__isnull=True,
    )
    mantencion = abiertas.filter(unidad_frio__tipo=UnidadFrio.TipoUnidad.MANTENCION)
    return PartidaProceso.objects.select_related(
        "detalle_recepcion__especie", "detalle_recepcion__recepcion",
        "detalle_recepcion__origen_sernapesca", "creado_por",
    ).annotate(
        tiene_hijas=Exists(PartidaProceso.objects.filter(partida_padre_id=OuterRef("pk"))),
        tiene_estancia=Exists(abiertas),
        en_mantencion=Exists(mantencion),
        procesamiento_iniciado=Exists(EventoProceso.objects.filter(partida_id=OuterRef("pk"))),
        ubicacion=Subquery(abiertas.order_by("pk").values("unidad_frio__nombre")[:1]),
    ).annotate(situacion=Case(
        When(estado=PartidaProceso.Estado.DIVIDIDA, then=Value("DIVIDIDA")),
        When(estado=PartidaProceso.Estado.CONSUMIDA, then=Value("CONSUMIDA")),
        When(estado=PartidaProceso.Estado.CERRADA, then=Value("CERRADA")),
        When(tiene_hijas=True, then=Value("NO_OPERABLE")),
        When(en_mantencion=True, procesamiento_iniciado=False, then=Value("EN_MANTENCION")),
        When(tiene_estancia=True, then=Value("NO_OPERABLE")),
        When(procesamiento_iniciado=True, then=Value("EN_PROCESO")),
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
        estado=PartidaProceso.Estado.ACTIVA, partida_padre__isnull=True,
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
