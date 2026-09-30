from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.trazabilidad.models import UnidadFrio
from apps.usuarios.models import Usuario
from apps.usuarios.permisos import ROL_JEFE, tiene_rol


@transaction.atomic
def configurar_umbral(*, usuario, unidad, horas):
    if not usuario.is_authenticated:
        raise PermissionDenied
    usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
    if (not usuario.is_active or not tiene_rol(usuario, ROL_JEFE)
            or (usuario.cambio_contrasena_pendiente and not usuario.is_superuser)):
        raise PermissionDenied
    unidad = UnidadFrio.objects.select_for_update().get(pk=unidad.pk)
    unidad.umbral_alerta_horas = horas
    unidad.full_clean()
    unidad.save(update_fields=["umbral_alerta_horas"])
    return unidad


def generar_reporte_apoyo_sernapesca(*, usuario, fecha_inicio=None, fecha_fin=None):
    from .excel_sernapesca import construir_excel_sernapesca
    from .forms import ReporteSernapescaForm
    from .selectors import obtener_abastecimiento_reporte, obtener_produccion_reporte, obtener_destino_reporte

    if not usuario.is_authenticated:
        raise PermissionDenied
    usuario = Usuario.objects.select_related("rol").get(pk=usuario.pk)
    if (not usuario.is_active or not tiene_rol(usuario, ROL_JEFE)
            or (usuario.cambio_contrasena_pendiente and not usuario.is_superuser)):
        raise PermissionDenied
    form = ReporteSernapescaForm({"fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin})
    if not form.is_valid():
        raise ValidationError("El rango de fechas no es válido.")
    inicio, fin = form.cleaned_data["fecha_inicio"], form.cleaned_data["fecha_fin"]
    datos = {
        "Abastecimiento": obtener_abastecimiento_reporte(inicio, fin),
        "Producción": obtener_produccion_reporte(inicio, fin),
        "Destino": obtener_destino_reporte(inicio, fin),
    }
    contenido = construir_excel_sernapesca(datos=datos, inicio=inicio, fin=fin, usuario=usuario, generado=timezone.now())
    desde = inicio.isoformat() if inicio else "desde_inicio"
    return contenido, f"reporte_apoyo_sernapesca_{desde}_{fin.isoformat()}.xlsx"
