from decimal import Decimal

from django.db.models import Case, CharField, Count, Exists, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.trazabilidad.models import Caja, UnidadFrio
from .models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja


ESTADOS = (
    ("ALMACENADA", "Almacenada"),
    ("PENDIENTE", "Pendiente de almacenar"),
    ("DESPACHADA", "Despachada"),
    ("BAJA", "Baja (registro histórico)"),
    ("REVISION", "Ubicación por revisar"),
)


def unidades_disponibles():
    return UnidadFrio.objects.filter(tipo=UnidadFrio.TipoUnidad.ALMACENAMIENTO, activo=True)


def cajas_con_inventario(queryset=None):
    cajas = Caja.objects.all() if queryset is None else queryset
    abiertas = EstanciaCaja.objects.filter(caja_id=OuterRef("pk"), fecha_hora_salida__isnull=True).order_by()
    return cajas.select_related("lote_produccion__especie", "detalle_despacho__despacho").annotate(
        numero_abiertas=Coalesce(Subquery(abiertas.values("caja_id").annotate(n=Count("pk")).values("n")), Value(0)),
        unidad_abierta_id=Subquery(abiertas.values("unidad_frio_id")[:1]),
        tipo_unidad_abierta=Subquery(abiertas.values("unidad_frio__tipo")[:1]),
        nombre_unidad_abierta=Subquery(abiertas.values("unidad_frio__nombre")[:1]),
        tiene_despacho=Exists(DetalleDespacho.objects.filter(caja_id=OuterRef("pk"))),
        tiene_baja=Exists(BajaCaja.objects.filter(caja_id=OuterRef("pk"))),
    ).annotate(estado_inventario=Case(
        When(tiene_despacho=True, then=Value("DESPACHADA")),
        When(tiene_baja=True, then=Value("BAJA")),
        When(numero_abiertas=0, then=Value("PENDIENTE")),
        When(numero_abiertas=1, tipo_unidad_abierta=UnidadFrio.TipoUnidad.ALMACENAMIENTO, then=Value("ALMACENADA")),
        default=Value("REVISION"), output_field=CharField(),
    )).annotate(
        estado_visible=Case(*[When(estado_inventario=k, then=Value(v)) for k, v in ESTADOS], output_field=CharField()),
        ubicacion_actual=Case(When(estado_inventario="ALMACENADA", then=F("nombre_unidad_abierta")), default=Value("—"), output_field=CharField()),
    )


def totales_stock():
    return dict(cajas=Count("pk"), peso_conocido=Coalesce(Sum("peso_neto_kg"), Value(Decimal("0.00"))),
                sin_peso=Count("pk", filter=Q(peso_neto_kg__isnull=True)))


def resumen_inventario(cajas):
    almacenadas = cajas.filter(estado_inventario="ALMACENADA")
    return {
        "stock": almacenadas.aggregate(**totales_stock()),
        "pendientes": cajas.filter(estado_inventario="PENDIENTE").count(),
        "revision": cajas.filter(estado_inventario="REVISION").count(),
        "por_especie": almacenadas.order_by().values("lote_produccion__especie_id", "lote_produccion__especie__nombre").annotate(**totales_stock()).order_by("lote_produccion__especie__nombre"),
        "por_lote": almacenadas.order_by().values("lote_produccion_id", "lote_produccion__codigo_lote", "lote_produccion__especie__nombre").annotate(**totales_stock()).order_by("lote_produccion__codigo_lote"),
        "por_camara": almacenadas.order_by().values("unidad_abierta_id", "nombre_unidad_abierta").annotate(**totales_stock()).order_by("nombre_unidad_abierta"),
        "por_camara_especie": almacenadas.order_by().values("unidad_abierta_id", "nombre_unidad_abierta", "lote_produccion__especie__nombre").annotate(**totales_stock()).order_by("nombre_unidad_abierta", "lote_produccion__especie__nombre"),
    }


def almacenamiento_caja(caja):
    historial = list(caja.estancias_inventario.select_related("unidad_frio", "ingresado_por", "retirado_por").order_by("fecha_hora_ingreso", "pk"))
    detalle = getattr(caja, "detalle_despacho", None)
    despacho = detalle.despacho if detalle else None
    anteriores = [e for e in historial if despacho and e.fecha_hora_salida
                  and e.fecha_hora_salida <= despacho.fecha_hora_despacho]
    ultima_salida = max((e.fecha_hora_salida for e in anteriores), default=None)
    ultimas = [e for e in anteriores if e.fecha_hora_salida == ultima_salida]
    anterior = ultimas[0] if len(ultimas) == 1 else None
    return {
        "inventario": caja,
        "historial_almacenamiento": historial,
        "despacho_caja": despacho, "estancia_anterior": anterior,
    }


def cajas_para_despacho():
    ingreso_futuro = EstanciaCaja.objects.filter(caja_id=OuterRef("pk"), fecha_hora_salida__isnull=True,
                                               fecha_hora_ingreso__gt=timezone.now())
    return cajas_con_inventario().annotate(ingreso_futuro=Exists(ingreso_futuro)).filter(
        estado_inventario="ALMACENADA", peso_neto_kg__isnull=False, ingreso_futuro=False,
    ).order_by("pk")


def despachos_con_totales():
    return Despacho.objects.select_related("registrado_por").annotate(
        cantidad_cajas=Count("detalles"),
        peso_conocido=Coalesce(Sum("detalles__caja__peso_neto_kg"), Value(Decimal("0.00"))),
        cajas_sin_peso=Count("detalles", filter=Q(detalles__caja__peso_neto_kg__isnull=True)),
    ).order_by("-fecha_hora_despacho", "-pk")
