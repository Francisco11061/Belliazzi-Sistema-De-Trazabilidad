from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import EventoUsuario, Rol, Usuario


class MantenimientoTecnico:
    """El Admin queda reservado al mantenimiento técnico; la operación usa servicios."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Rol)
class RolAdmin(MantenimientoTecnico, admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activo")
    search_fields = ("codigo", "nombre")
    list_filter = ("activo",)


@admin.register(Usuario)
class UsuarioAdmin(MantenimientoTecnico, UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (
            "Datos adicionales",
            {"fields": ("rol", "cambio_contrasena_pendiente", "creado_por")},
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Datos adicionales",
            {"fields": ("rol", "cambio_contrasena_pendiente", "creado_por")},
        ),
    )
    list_display = (
        "username",
        "first_name",
        "last_name",
        "rol",
        "is_active",
        "is_staff",
    )
    list_filter = ("rol", "is_active", "is_staff", "is_superuser")


@admin.register(EventoUsuario)
class EventoUsuarioAdmin(MantenimientoTecnico, admin.ModelAdmin):
    list_display = ("usuario", "tipo", "actor", "fecha_hora")
    list_filter = ("tipo",)
    search_fields = ("usuario__username", "actor__username")
    readonly_fields = ("usuario", "actor", "tipo", "fecha_hora", "rol_anterior", "rol_nuevo")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
