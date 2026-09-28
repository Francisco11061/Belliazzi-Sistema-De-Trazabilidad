from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from .rutas import validar_etapas_ruta
from .selectors import cajas_con_peso

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
    RutaProceso,
    EtapaRutaProceso,
)


@admin.register(Especie)
class EspecieAdmin(admin.ModelAdmin):
    list_display = ("nombre", "codigo_lote", "activo")
    search_fields = ("nombre", "codigo_lote")
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
    readonly_fields = ("ruta_proceso",)
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

    def get_readonly_fields(self, request, obj=None):
        if obj and (obj.codigo in {"PROCESAMIENTO", "DESCABEZADO", "FILETEO", "EMPARRILLADO"}
                    or obj.etapas_ruta.exists() or obj.eventos.exists()):
            return ("codigo", "nombre")
        return ()


class EtapasRutaFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if self.instance.pk and self.instance.partidas.exists():
            if any(form.has_changed() for form in self.forms):
                raise ValidationError("Las etapas de una ruta utilizada no pueden modificarse.")
            return
        etapas = [form.instance for form in self.forms
                  if form.cleaned_data and not form.cleaned_data.get("DELETE", False)]
        validar_etapas_ruta(sorted(etapas, key=lambda etapa: etapa.orden))


class EtapaRutaProcesoInline(admin.TabularInline):
    model = EtapaRutaProceso
    formset = EtapasRutaFormSet
    extra = 1
    fields = ("orden", "tipo_proceso")

    def get_readonly_fields(self, request, obj=None):
        return self.fields if obj and obj.partidas.exists() else ()

    def has_add_permission(self, request, obj=None):
        return super().has_add_permission(request, obj) and not (obj and obj.partidas.exists())

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.partidas.exists())

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "tipo_proceso":
            kwargs["queryset"] = TipoProceso.objects.filter(activo=True).exclude(codigo="PROCESAMIENTO")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(RutaProceso)
class RutaProcesoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "especie", "predeterminada", "activo")
    list_filter = ("especie", "activo", "predeterminada")
    search_fields = ("nombre", "especie__nombre")
    list_select_related = ("especie",)
    inlines = (EtapaRutaProcesoInline,)

    def get_object(self, request, object_id, from_field=None):
        objeto = super().get_object(request, object_id, from_field)
        # changeform_view ya abre una transacción para POST. Serializa la
        # edición del catálogo con la primera asignación operativa de la ruta.
        if objeto and request.method == "POST":
            return RutaProceso.objects.select_for_update().get(pk=objeto.pk)
        return objeto

    def get_readonly_fields(self, request, obj=None):
        return ("especie", "nombre", "descripcion") if obj and obj.partidas.exists() else ()

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.partidas.exists())


@admin.register(EtapaRutaProceso)
class EtapaRutaProcesoAdmin(admin.ModelAdmin):
    list_display = ("ruta", "orden", "tipo_proceso")
    list_filter = ("ruta__especie", "tipo_proceso")
    list_select_related = ("ruta__especie", "tipo_proceso")
    search_fields = ("ruta__nombre", "tipo_proceso__nombre")
    actions = None

    def get_object(self, request, object_id, from_field=None):
        objeto = super().get_object(request, object_id, from_field)
        if objeto and request.method == "POST":
            RutaProceso.objects.select_for_update().get(pk=objeto.ruta_id)
        return objeto

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and not (obj and obj.ruta.partidas.exists())

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.ruta.partidas.exists())

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "tipo_proceso":
            kwargs["queryset"] = TipoProceso.objects.filter(activo=True).exclude(codigo="PROCESAMIENTO")
        if db_field.name == "ruta":
            kwargs["queryset"] = RutaProceso.objects.filter(partidas__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(EventoProceso)
class EventoProcesoAdmin(admin.ModelAdmin):
    readonly_fields = ("etapa_ruta",)
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


class RegistroHistoricoAdmin(admin.ModelAdmin):
    """Consulta de auditoría; las altas operativas pasan por los services."""
    actions = None
    list_select_related = ("partida__detalle_recepcion__especie", "registrado_por", "evento_proceso__tipo_proceso")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Pesaje)
class PesajeAdmin(RegistroHistoricoAdmin):
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
class MermaProcesoAdmin(RegistroHistoricoAdmin):
    list_display = (
        "id",
        "partida",
        "cantidad_kg",
        "tipo",
        "evento_proceso",
        "motivo",
        "fecha_hora_evento",
        "registrado_por",
    )
    list_filter = ("tipo", "fecha_hora_evento")
    search_fields = (
        "motivo",
        "partida__detalle_recepcion__especie__nombre",
    )


@admin.register(LoteProduccion)
class LoteProduccionAdmin(RegistroHistoricoAdmin):
    list_select_related = ("especie", "ruta_proceso", "registrado_por")
    list_display = (
        "codigo_lote",
        "especie",
        "ruta_proceso",
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
class ConsumoLoteAdmin(RegistroHistoricoAdmin):
    list_select_related = ("partida__detalle_recepcion__especie", "lote_produccion")
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
class CajaAdmin(RegistroHistoricoAdmin):
    readonly_fields = ("identificador_qr",)
    list_select_related = ("lote_produccion", "registrado_por")
    list_display = (
        "id",
        "identificador_qr",
        "codigo_caja",
        "lote_produccion",
        "peso_derivado",
        "peso_neto_kg",
        "fecha_armado",
        "registrado_por",
    )
    search_fields = (
        "codigo_caja",
        "identificador_qr",
        "lote_produccion__codigo_lote",
    )
    list_filter = ("fecha_armado",)

    def get_queryset(self, request):
        return cajas_con_peso()

    @admin.display(description="Peso teórico (kg)", ordering="peso_teorico_kg")
    def peso_derivado(self, obj):
        return obj.peso_teorico_kg


@admin.register(ComposicionCaja)
class ComposicionCajaAdmin(RegistroHistoricoAdmin):
    list_select_related = ("caja", "presentacion")
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
