from decimal import Decimal

from django.db.models import Count, Sum

from apps.trazabilidad.models import Caja


def cajas_disponibles():
    return Caja.objects.filter(
        estancias_inventario__isnull=False,
        estancias_inventario__fecha_hora_salida__isnull=True,
        detalle_despacho__isnull=True,
        baja__isnull=True,
    ).distinct()


def resumen_stock():
    return cajas_disponibles().aggregate(
        total_cajas=Count("pk"),
        peso_total_kg=Sum("peso_total_kg", default=Decimal("0.00")),
    )
