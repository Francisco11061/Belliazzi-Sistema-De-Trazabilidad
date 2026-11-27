from django import template
from django.utils import timezone

from apps.trazabilidad.formato import formatear_duracion

register = template.Library()


def _texto(delta):
    return formatear_duracion(delta)


@register.filter
def permanencia(inicio, fin=None):
    """Tiempo transcurrido entre dos instantes; si no hay término, hasta ahora (en curso)."""
    if not inicio:
        return ""
    texto = _texto((fin or timezone.now()) - inicio)
    return texto if fin else f"{texto} (en curso)"
