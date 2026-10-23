"""Organización de lectura del trabajo pendiente; no persiste estados nuevos."""
from django.db.models import Case, CharField, Count, DateTimeField, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce

from .models import EstanciaPartida, EventoProceso, Pesaje
from .selectors import partidas_con_disponibilidad, partidas_para_lote

CATEGORIAS = (
    ("atender", "Por atender"),
    ("empaque", "Listos para empaque"),
    ("historial", "Historial"),
)


def seguimientos_operativos():
    abiertas = EstanciaPartida.objects.filter(partida_id=OuterRef("pk"), fecha_hora_salida__isnull=True)
    eventos = EventoProceso.objects.filter(partida_id=OuterRef("pk"))
    ultimos_eventos = eventos.annotate(fecha=Coalesce("fecha_hora_termino", "fecha_hora_inicio"))
    salidas = EstanciaPartida.objects.filter(partida_id=OuterRef("pk"), fecha_hora_salida__isnull=False)
    pesos = Pesaje.objects.filter(partida_id=OuterRef("pk"), tipo=Pesaje.POSTPROCESO)
    partidas = partidas_con_disponibilidad().annotate(
        numero_abiertas=Subquery(abiertas.order_by().values("partida_id").annotate(n=Count("pk")).values("n")),
        ultima_operacion=Subquery(ultimos_eventos.order_by("-fecha", "-pk").values("fecha")[:1]),
        ultima_salida=Subquery(salidas.order_by("-fecha_hora_salida", "-pk").values("fecha_hora_salida")[:1]),
        ultimo_pesaje=Subquery(pesos.order_by("-fecha_hora_evento", "-pk").values("fecha_hora_evento")[:1]),
    )
    # Reutiliza el selector de asignación; no basta con terminar una etapa.
    listos = Q(pk__in=partidas_para_lote().values("pk"))
    asignados = Q(situacion="LISTA_PARA_EMPAQUE", numero_postprocesos=1,
                  ruta_proceso__isnull=False, peso_final_kg__gt=0,
                  tiene_consumos=True, disponible_lote_kg=0)
    historicos = Q(situacion__in=["DIVIDIDA", "CONSUMIDA", "CERRADA"]) | asignados
    revision_frio = Q(situacion__in=["EN_MANTENCION", "EN_CONGELACION"]) & ~Q(numero_abiertas=1)
    revision_mantencion = Q(situacion="EN_MANTENCION") & (
        Q(procesamiento_iniciado=True) | Q(ruta_proceso__isnull=False))
    return partidas.annotate(
        categoria_operativa=Case(
            When(historicos, then=Value("historial")),
            When(listos, then=Value("empaque")),
            default=Value("atender"), output_field=CharField(),
        ),
        revision_operativa=Case(
            When(situacion="NO_OPERABLE", then=Value("El historial está incompleto o es inconsistente. Revisa el seguimiento antes de operar.")),
            When(revision_frio | revision_mantencion, then=Value("Los registros de ubicación y proceso requieren revisión antes de operar.")),
            When(Q(situacion="LISTA_PARA_EMPAQUE") & ~listos & ~asignados,
                 then=Value("Revisa la ruta, el pesaje y las asignaciones: no hay un saldo válido para asignar.")),
            default=Value(""), output_field=CharField(),
        ),
        fecha_prioridad=Coalesce(Case(
            When(situacion__in=["EN_MANTENCION", "EN_CONGELACION"], then=F("ingreso_actual")),
            When(situacion="EN_PROCESO", then=F("ultima_operacion")),
            When(situacion__in=["DISPONIBLE", "PENDIENTE_PESAJE"], then=F("ultima_salida")),
            When(situacion="LISTA_PARA_EMPAQUE", then=F("ultimo_pesaje")),
            output_field=DateTimeField(),
        ), F("creado_en")),
    ).order_by("fecha_prioridad", "pk")
