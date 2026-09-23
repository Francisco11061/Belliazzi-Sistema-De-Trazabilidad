from django.contrib import admin

from .models import (
    LoteProduccion,
    ConsumoLote,
    PresentacionBolsa,
    Caja,
    ComposicionCaja,
    Correccion,
    DetalleRecepcion,
    Especie,
    EstanciaPartida,
    EventoProceso,
    MermaProceso,
    OrigenSernapesca,
    PartidaProceso,
    Pesaje,
    Recepcion,
    TipoProceso,
    UnidadFrio,
)


@admin.register(Especie)
class EspecieAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo")
    search_fields = ("nombre",)
    list_filter = ("activo",)


@admin.register(OrigenSernapesca)
class OrigenSernapescaAdmin(admin.ModelAdmin):
    list_display = ("folio_origen", "tipo_origen", "codigo_agente", "proveedor")
    search_fields = ("folio_origen", "codigo_agente", "proveedor")


@admin.register(Recepcion)
class RecepcionAdmin(admin.ModelAdmin):
    list_display = ("id", "fecha_hora_recepcion", "registrado_por", "creado_en")
    search_fields = (
        "registrado_por__username",
        "registrado_por__first_name",
        "registrado_por__last_name",
    )
    list_filter = ("fecha_hora_recepcion",)


@admin.register(DetalleRecepcion)
class DetalleRecepcionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recepcion",
        "especie",
        "origen_sernapesca",
        "peso_origen_kg",
        "peso_recepcion_kg",
    )
    search_fields = ("origen_sernapesca__folio_origen", "especie__nombre")
    list_filter = ("especie",)


@admin.register(PartidaProceso)
class PartidaProcesoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "detalle_recepcion",
        "partida_padre",
        "cantidad_inicial_kg",
        "estado",
        "creado_por",
    )
    list_filter = ("estado",)
    search_fields = (
        "detalle_recepcion__especie__nombre",
        "creado_por__username",
    )


@admin.register(TipoProceso)
class TipoProcesoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activo")
    search_fields = ("codigo", "nombre")
    list_filter = ("activo",)


@admin.register(EventoProceso)
class EventoProcesoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "partida",
        "tipo_proceso",
        "fecha_hora_inicio",
        "fecha_hora_termino",
        "iniciado_por",
    )
    list_filter = ("tipo_proceso", "fecha_hora_inicio")
    search_fields = (
        "tipo_proceso__nombre",
        "partida__detalle_recepcion__especie__nombre",
    )


@admin.register(UnidadFrio)
class UnidadFrioAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "activo")
    list_filter = ("tipo", "activo")
    search_fields = ("nombre",)


@admin.register(EstanciaPartida)
class EstanciaPartidaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "partida",
        "unidad_frio",
        "fecha_hora_ingreso",
        "fecha_hora_salida",
    )
    list_filter = ("unidad_frio", "fecha_hora_ingreso")


@admin.register(Pesaje)
class PesajeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "partida",
        "tipo",
        "peso_kg",
        "fecha_hora_evento",
        "registrado_por",
    )
    list_filter = ("tipo", "fecha_hora_evento")
    search_fields = (
        "tipo",
        "partida__detalle_recepcion__especie__nombre",
    )


@admin.register(MermaProceso)
class MermaProcesoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "partida",
        "cantidad_kg",
        "motivo",
        "fecha_hora_evento",
        "registrado_por",
    )
    list_filter = ("fecha_hora_evento",)
    search_fields = (
        "motivo",
        "partida__detalle_recepcion__especie__nombre",
    )


@admin.register(LoteProduccion)
class LoteProduccionAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_lote",
        "especie",
        "fecha_elaboracion",
        "fecha_vencimiento",
        "registrado_por",
    )
    search_fields = (
        "codigo_lote",
        "especie__nombre",
    )
    list_filter = (
        "especie",
        "fecha_elaboracion",
    )


@admin.register(ConsumoLote)
class ConsumoLoteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "partida",
        "lote_produccion",
        "cantidad_kg_utilizada",
    )
    search_fields = (
        "lote_produccion__codigo_lote",
        "partida__detalle_recepcion__especie__nombre",
    )


@admin.register(PresentacionBolsa)
class PresentacionBolsaAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "peso_nominal_kg",
        "activo",
    )
    search_fields = ("nombre",)
    list_filter = ("activo",)


@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_caja",
        "lote_produccion",
        "peso_total_kg",
        "fecha_armado",
        "registrado_por",
    )
    search_fields = (
        "codigo_caja",
        "lote_produccion__codigo_lote",
    )
    list_filter = ("fecha_armado",)


@admin.register(ComposicionCaja)
class ComposicionCajaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "caja",
        "presentacion",
        "cantidad",
        "peso_unitario_kg",
    )
    search_fields = (
        "caja__codigo_caja",
        "presentacion__nombre",
    )
    list_filter = ("presentacion",)


@admin.register(Correccion)
class CorreccionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "entidad_afectada",
        "identificador_registro",
        "campo",
        "usuario",
        "fecha_hora",
    )
    search_fields = (
        "entidad_afectada",
        "identificador_registro",
        "campo",
        "usuario__username",
    )
    list_filter = (
        "entidad_afectada",
        "fecha_hora",
    )
