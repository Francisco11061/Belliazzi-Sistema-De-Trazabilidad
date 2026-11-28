"""Filtros de formato disponibles en todas las plantillas (ver TEMPLATES.builtins)."""
from django import template
from django.utils.html import format_html

from apps.trazabilidad.formato import formatear_horas, formatear_numero

register = template.Library()


@register.filter
def num(valor, decimales=2):
    """Número en formato chileno sin ceros sobrantes: 1.234,5."""
    return formatear_numero(valor, decimales)


@register.filter
def horas_legibles(valor):
    """Cantidad de horas en días u horas, según corresponda."""
    return formatear_horas(valor)


@register.simple_tag
def procedencia(partida):
    """«Proveedor · llegó el 25/11», con la fecha y hora exactas al pasar el mouse."""
    llegada = partida.llegada
    return format_html('{} · <time datetime="{}" data-tip="{}">llegó el {}</time>', partida.proveedor,
                       llegada.isoformat(), f"{llegada:%d/%m/%Y %H:%M}", f"{llegada:%d/%m}")


@register.simple_tag
def fecha_con_detalle(fecha, texto):
    """Texto corto («hace 2 h») con la fecha exacta al pasar el mouse."""
    if not fecha:
        return texto
    from django.utils import timezone
    local = timezone.localtime(fecha)
    return format_html('<time datetime="{}" data-tip="{}">{}</time>', local.isoformat(), f"{local:%d/%m/%Y %H:%M}", texto)
