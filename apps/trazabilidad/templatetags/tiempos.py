from django import template
from django.utils import timezone

register = template.Library()


def _texto(delta):
    minutos = max(0, int(delta.total_seconds())) // 60
    if minutos < 1:
        return "menos de 1 min"
    horas, minutos = divmod(minutos, 60)
    return f"{horas} h {minutos:02d} min"


@register.filter
def permanencia(inicio, fin=None):
    """Tiempo transcurrido entre dos instantes; si no hay término, hasta ahora (en curso)."""
    if not inicio:
        return ""
    texto = _texto((fin or timezone.now()) - inicio)
    return texto if fin else f"{texto} (en curso)"
