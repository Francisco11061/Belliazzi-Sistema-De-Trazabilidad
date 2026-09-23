from django.conf import settings
from django.db import models

from apps.trazabilidad.models import Caja, UnidadFrio


class EstanciaCaja(models.Model):
    caja = models.ForeignKey(
        Caja,
        on_delete=models.PROTECT,
        related_name="estancias_inventario",
    )
    unidad_frio = models.ForeignKey(
        UnidadFrio,
        on_delete=models.PROTECT,
        related_name="estancias_cajas",
    )
    fecha_hora_ingreso = models.DateTimeField()
    fecha_hora_salida = models.DateTimeField(null=True, blank=True)
    ingresado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cajas_ingresadas_inventario",
    )
    retirado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cajas_retiradas_inventario",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(fecha_hora_salida__isnull=True)
                    | models.Q(fecha_hora_salida__gte=models.F("fecha_hora_ingreso"))
                ),
                name="estancia_caja_salida_mayor_igual_ingreso",
            ),
        ]

    def __str__(self):
        return f"{self.caja} - {self.unidad_frio}"


class Despacho(models.Model):
    fecha_hora_despacho = models.DateTimeField()
    destino = models.CharField(max_length=200, blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="despachos_registrados",
    )
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_hora_despacho", "-id"]

    def __str__(self):
        return f"Despacho {self.pk}"


class DetalleDespacho(models.Model):
    despacho = models.ForeignKey(
        Despacho,
        on_delete=models.PROTECT,
        related_name="detalles",
    )
    caja = models.OneToOneField(
        Caja,
        on_delete=models.PROTECT,
        related_name="detalle_despacho",
    )

    def __str__(self):
        return f"{self.despacho} - {self.caja}"


class BajaCaja(models.Model):
    caja = models.OneToOneField(
        Caja,
        on_delete=models.PROTECT,
        related_name="baja",
    )
    motivo = models.CharField(max_length=200)
    fecha_hora_evento = models.DateTimeField()
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bajas_caja_registradas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Baja {self.caja}"
