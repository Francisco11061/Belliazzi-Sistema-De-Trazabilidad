"""Integración Axes: persistencia oficial, IP confiable y respuesta de bloqueo."""
from ipaddress import ip_address, ip_network
from math import ceil

from axes.handlers.database import AxesDatabaseHandler
from axes.helpers import get_cool_off
from django.conf import settings
from django.http import QueryDict
from django.utils import timezone


def _ip(valor):
    valor = valor.strip()
    try:
        return ip_address(valor)
    except ValueError:
        # ALB puede adjuntar puerto a IPv4 o a [IPv6].
        if valor.startswith('[') and ']:' in valor:
            direccion, puerto = valor[1:].split(']:', 1)
        elif valor.count(':') == 1:
            direccion, puerto = valor.rsplit(':', 1)
        else:
            raise
        if not puerto.isdecimal() or not 0 < int(puerto) <= 65535:
            raise ValueError('Puerto inválido')
        return ip_address(direccion)


def ip_cliente(request):
    """Camina desde el peer hacia atrás; ignora todo lo anterior al primer no confiable."""
    try:
        peer = _ip(request.META.get('REMOTE_ADDR', ''))
    except ValueError:
        return None
    redes = [ip_network(cidr) for cidr in settings.TRUSTED_PROXY_CIDRS]
    confiable = lambda ip: any(ip in red for red in redes)
    if not confiable(peer):
        return str(peer)
    encabezado = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not encabezado:
        return str(peer)
    partes = encabezado.split(',')
    if len(partes) > 16:
        return str(peer)
    actual = peer
    for parte in reversed(partes):
        if not confiable(actual):
            break
        try:
            actual = _ip(parte)
        except ValueError:
            return str(peer)
    return str(actual)


class IntentosSinCredencialesHandler(AxesDatabaseHandler):
    """No guarda contenido GET/POST; conserva el conteo y bloqueo transaccional de Axes."""

    def user_login_failed(self, sender, credentials, request=None, **kwargs):
        if request is None:
            return super().user_login_failed(sender, credentials, request, **kwargs)
        get, post = request.GET, request.POST
        try:
            request.GET, request.POST = QueryDict(), QueryDict()
            return super().user_login_failed(sender, credentials, request, **kwargs)
        finally:
            request.GET, request.POST = get, post


def respuesta_bloqueo(request, original_response=None, credentials=None):
    from config.errores import respuesta_error
    handler = AxesDatabaseHandler()
    intentos = handler.get_user_attempts(request, credentials)
    ultimo = max((fecha for qs in intentos for fecha in qs.values_list('attempt_time', flat=True)), default=timezone.now())
    segundos = max(1, ceil((ultimo + get_cool_off(request) - timezone.now()).total_seconds()))
    response = respuesta_error(429, login_bloqueado=True)
    response['Retry-After'] = str(segundos)
    return response
