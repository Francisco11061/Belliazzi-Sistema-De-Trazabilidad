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


class PartidaProceso(models.Model):
    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        DIVIDIDA = "DIVIDIDA", "Dividida"
        CONSUMIDA = "CONSUMIDA", "Consumida"
        CERRADA = "CERRADA", "Cerrada"

    detalle_recepcion = models.ForeignKey(
        DetalleRecepcion,
        on_delete=models.PROTECT,
        related_name="partidas",
    )
    partida_padre = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subpartidas",
    )
    cantidad_inicial_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.ACTIVA,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="partidas_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Partida {self.pk} - {self.detalle_recepcion.especie.nombre}"


class TipoProceso(models.Model):
    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=80, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class EventoProceso(models.Model):
    partida = models.ForeignKey(
        PartidaProceso,
        on_delete=models.PROTECT,
        related_name="eventos",
    )
    tipo_proceso = models.ForeignKey(
        TipoProceso,
        on_delete=models.PROTECT,
        related_name="eventos",
    )
    fecha_hora_inicio = models.DateTimeField()
    fecha_hora_termino = models.DateTimeField(null=True, blank=True)
    iniciado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="procesos_iniciados",
    )
    finalizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="procesos_finalizados",
    )
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(fecha_hora_termino__isnull=True)
                    | models.Q(fecha_hora_termino__gte=models.F("fecha_hora_inicio"))
                ),
                name="evento_fin_mayor_igual_inicio",
            ),
        ]

    def __str__(self):
        return f"{self.tipo_proceso} - Partida {self.partida_id}"


class UnidadFrio(models.Model):
    class TipoUnidad(models.TextChoices):
        MANTENCION = "MANTENCION", "Mantención"
        TUNEL_CONGELADO = "TUNEL_CONGELADO", "Túnel de congelado"
        ALMACENAMIENTO = "ALMACENAMIENTO", "Almacenamiento"

    nombre = models.CharField(max_length=80, unique=True)
    tipo = models.CharField(max_length=30, choices=TipoUnidad.choices)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class EstanciaPartida(models.Model):
    partida = models.ForeignKey(
        PartidaProceso,
        on_delete=models.PROTECT,
        related_name="estancias_frio",
    )
    unidad_frio = models.ForeignKey(
        UnidadFrio,
        on_delete=models.PROTECT,
        related_name="estancias_partidas",
    )
    fecha_hora_ingreso = models.DateTimeField()
    fecha_hora_salida = models.DateTimeField(null=True, blank=True)
    ingresado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="estancias_partida_ingresadas",
    )
    retirado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="estancias_partida_retiradas",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(fecha_hora_salida__isnull=True)
                    | models.Q(fecha_hora_salida__gte=models.F("fecha_hora_ingreso"))
                ),
                name="estancia_partida_salida_mayor_igual_ingreso",
            ),
        ]

    def __str__(self):
        return f"Partida {self.partida_id} - {self.unidad_frio}"


class Pesaje(models.Model):
    partida = models.ForeignKey(
        PartidaProceso,
        on_delete=models.PROTECT,
        related_name="pesajes",
    )
    evento_proceso = models.ForeignKey(
        EventoProceso,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pesajes",
    )
    tipo = models.CharField(max_length=50)
    peso_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    fecha_hora_evento = models.DateTimeField()
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pesajes_registrados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Pesaje {self.pk} - Partida {self.partida_id}"


class MermaProceso(models.Model):
    partida = models.ForeignKey(
        PartidaProceso,
        on_delete=models.PROTECT,
        related_name="mermas",
    )
    evento_proceso = models.ForeignKey(
        EventoProceso,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mermas",
    )
    cantidad_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    motivo = models.CharField(max_length=200)
    fecha_hora_evento = models.DateTimeField()
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mermas_registradas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Merma {self.pk} - Partida {self.partida_id}"
