"""Filtros de formato disponibles en todas las plantillas (ver TEMPLATES.builtins)."""
from django import template

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
