from django.contrib import admin

from .models import BajaCaja, Despacho, DetalleDespacho, EstanciaCaja


@admin.register(EstanciaCaja)
class EstanciaCajaAdmin(admin.ModelAdmin):
    # El historial es consultable; las operaciones usan services con bloqueo de Caja.
    actions = None
    list_select_related = ("caja", "unidad_frio", "ingresado_por")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_display = (
        "id",
        "caja",
        "unidad_frio",
        "fecha_hora_ingreso",
        "fecha_hora_salida",
        "ingresado_por",
    )
    search_fields = (
        "caja__codigo_caja",
        "unidad_frio__nombre",
    )
    list_filter = (
        "unidad_frio",
        "fecha_hora_ingreso",
    )


@admin.register(Despacho)
class DespachoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "fecha_hora_despacho",
        "destino",
        "registrado_por",
    )
    search_fields = (
        "destino",
        "registrado_por__username",
    )
    list_filter = ("fecha_hora_despacho",)


@admin.register(DetalleDespacho)
class DetalleDespachoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "despacho",
        "caja",
    )
    search_fields = (
        "caja__codigo_caja",
        "despacho__destino",
    )


@admin.register(BajaCaja)
class BajaCajaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "caja",
        "motivo",
        "fecha_hora_evento",
        "registrado_por",
    )
    search_fields = (
        "caja__codigo_caja",
        "motivo",
    )
    list_filter = ("fecha_hora_evento",)
