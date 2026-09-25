from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction


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
    ruta_proceso = models.ForeignKey(
        "RutaProceso", null=True, blank=True, on_delete=models.PROTECT,
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
    etapa_ruta = models.ForeignKey(
        "EtapaRutaProceso", null=True, blank=True, on_delete=models.PROTECT,
        related_name="eventos",
    )
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


class RutaProceso(models.Model):
    especie = models.ForeignKey(Especie, on_delete=models.PROTECT, related_name="rutas_proceso")
    nombre = models.CharField(max_length=120)
    descripcion = models.TextField(blank=True)
    predeterminada = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["especie__nombre", "nombre"]
        constraints = [models.UniqueConstraint(fields=["especie", "nombre"], name="ruta_nombre_unico_por_especie")]

    def __str__(self):
        return f"{self.especie} / {self.nombre}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            original = RutaProceso.objects.select_for_update().get(pk=self.pk)
            if original.partidas.exists() and any(
                getattr(original, campo) != getattr(self, campo)
                for campo in ("especie_id", "nombre", "descripcion")
            ):
                raise ValidationError("Una ruta utilizada conserva su especie, nombre y descripción. Cree otra ruta.")
        super().save(*args, **kwargs)


class EtapaRutaProceso(models.Model):
    ruta = models.ForeignKey(RutaProceso, on_delete=models.CASCADE, related_name="etapas")
    tipo_proceso = models.ForeignKey(TipoProceso, on_delete=models.PROTECT, related_name="etapas_ruta")
    orden = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["ruta_id", "orden"]
        constraints = [
            models.UniqueConstraint(fields=["ruta", "orden"], name="etapa_orden_unico_por_ruta"),
            models.CheckConstraint(condition=models.Q(orden__gt=0), name="etapa_orden_positivo"),
        ]

    def __str__(self):
        return f"{self.orden}. {self.tipo_proceso}"

    def clean(self):
        super().clean()
        if self.tipo_proceso_id:
            tipo = self.tipo_proceso
            if tipo.codigo == "PROCESAMIENTO":
                raise ValidationError({"tipo_proceso": "Procesamiento es el evento general, no una etapa."})
            if not tipo.activo and not (self.pk and self.ruta.partidas.exists()):
                raise ValidationError({"tipo_proceso": "Seleccione un tipo de proceso activo."})

    def _bloquear_rutas_editables(self):
        ids = {self.ruta_id}
        if self.pk:
            ids.update(EtapaRutaProceso.objects.filter(pk=self.pk).values_list("ruta_id", flat=True))
        for ruta in RutaProceso.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
            if ruta.partidas.exists():
                raise ValidationError("No se pueden agregar, modificar ni borrar etapas de una ruta utilizada.")

    @transaction.atomic
    def save(self, *args, **kwargs):
        self._bloquear_rutas_editables()
        self.full_clean()
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        self._bloquear_rutas_editables()
        return super().delete(*args, **kwargs)


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
    POSTPROCESO = "POSTPROCESO"

    @property
    def tipo_visible(self):
        return "Peso postproceso" if self.tipo == self.POSTPROCESO else self.tipo

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
    class Tipo(models.TextChoices):
        MERMA = "MERMA", "Merma"
        DESCARTE = "DESCARTE", "Descarte"
        PERDIDA = "PERDIDA", "Pérdida"

    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.MERMA)

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


class LoteProduccion(models.Model):
    codigo_lote = models.CharField(max_length=80, unique=True)
    especie = models.ForeignKey(
        Especie,
        on_delete=models.PROTECT,
        related_name="lotes_produccion",
    )
    fecha_elaboracion = models.DateField()
    fecha_vencimiento = models.DateField(null=True, blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lotes_produccion_registrados",
    )
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_elaboracion", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(fecha_vencimiento__isnull=True)
                    | models.Q(fecha_vencimiento__gte=models.F("fecha_elaboracion"))
                ),
                name="lote_vencimiento_mayor_igual_elaboracion",
            ),
        ]

    def __str__(self):
        return self.codigo_lote


class ConsumoLote(models.Model):
    partida = models.ForeignKey(
        PartidaProceso,
        on_delete=models.PROTECT,
        related_name="consumos_lote",
    )
    lote_produccion = models.ForeignKey(
        LoteProduccion,
        on_delete=models.PROTECT,
        related_name="consumos",
    )
    cantidad_kg_utilizada = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    def __str__(self):
        return f"Partida {self.partida_id} -> {self.lote_produccion}"


class PresentacionBolsa(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    peso_nominal_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["peso_nominal_kg", "nombre"]

    def __str__(self):
        return self.nombre


class Caja(models.Model):
    lote_produccion = models.ForeignKey(
        LoteProduccion,
        on_delete=models.PROTECT,
        related_name="cajas",
    )
    codigo_caja = models.CharField(max_length=100, unique=True)
    peso_total_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    fecha_armado = models.DateTimeField()
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cajas_registradas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.codigo_caja


class ComposicionCaja(models.Model):
    caja = models.ForeignKey(
        Caja,
        on_delete=models.PROTECT,
        related_name="composiciones",
    )
    presentacion = models.ForeignKey(
        PresentacionBolsa,
        on_delete=models.PROTECT,
        related_name="composiciones_caja",
    )
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    peso_unitario_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["caja", "presentacion"],
                name="composicion_caja_presentacion_unica",
            ),
        ]

    def __str__(self):
        return f"{self.caja} - {self.cantidad} x {self.presentacion}"


class Correccion(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="correcciones_realizadas",
    )
    entidad_afectada = models.CharField(max_length=100)
    identificador_registro = models.CharField(max_length=100)
    campo = models.CharField(max_length=100)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    motivo = models.TextField()
    fecha_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_hora"]

    def __str__(self):
        return f"{self.entidad_afectada} {self.identificador_registro} - {self.campo}"
