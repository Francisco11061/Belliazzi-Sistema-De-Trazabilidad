from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Especie(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class OrigenSernapesca(models.Model):
    folio_origen = models.CharField(max_length=50, db_index=True)
    tipo_origen = models.CharField(max_length=80, blank=True)
    codigo_agente = models.CharField(max_length=50, blank=True)
    proveedor = models.CharField(max_length=150, blank=True)

    def __str__(self):
        return self.folio_origen


class Recepcion(models.Model):
    fecha_hora_recepcion = models.DateTimeField()
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recepciones_registradas",
    )
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_hora_recepcion"]

    def __str__(self):
        return f"Recepción {self.pk}"


class DetalleRecepcion(models.Model):
    recepcion = models.ForeignKey(
        Recepcion,
        on_delete=models.PROTECT,
        related_name="detalles",
    )
    origen_sernapesca = models.ForeignKey(
        OrigenSernapesca,
        on_delete=models.PROTECT,
        related_name="detalles_recepcion",
    )
    especie = models.ForeignKey(
        Especie,
        on_delete=models.PROTECT,
        related_name="detalles_recepcion",
    )
    peso_origen_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    peso_recepcion_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    def __str__(self):
        return f"Recepción {self.recepcion_id} - {self.especie.nombre}"
