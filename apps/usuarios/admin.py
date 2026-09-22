from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Rol, Usuario


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activo")
    search_fields = ("codigo", "nombre")
    list_filter = ("activo",)


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
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
