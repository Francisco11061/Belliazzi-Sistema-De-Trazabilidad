from django.contrib import admin

from .models import DetalleRecepcion, Especie, OrigenSernapesca, Recepcion


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
