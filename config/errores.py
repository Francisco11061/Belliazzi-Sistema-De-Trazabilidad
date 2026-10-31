"""Errores sin depender de sesión, usuario ni consultas a base de datos."""
from django.http import HttpResponse
from django.template.loader import render_to_string

MENSAJES = {
    400: ('No pudimos procesar la solicitud', 'Los datos de la solicitud no son válidos. Vuelve a intentarlo.'),
    403: ('Acceso restringido', 'No tienes permisos para acceder a esta sección. Si necesitas acceder, consulta con el encargado del sistema.'),
    404: ('No encontramos esta página', 'La dirección puede ser incorrecta o el registro solicitado ya no está disponible.'),
    429: ('Demasiados intentos', 'Has realizado demasiados intentos en poco tiempo. Inténtalo nuevamente más tarde.'),
    500: ('No pudimos completar tu solicitud', 'Ocurrió un problema inesperado. Inténtalo nuevamente. Si continúa, informa al encargado del sistema.'),
}


def respuesta_error(codigo, *, login_bloqueado=False, csrf=False):
    titulo, mensaje = MENSAJES[codigo]
    if login_bloqueado:
        titulo = 'Demasiados intentos de inicio de sesión'
        mensaje = 'Por seguridad, el acceso se ha limitado temporalmente. Inténtalo nuevamente más tarde.'
    elif csrf:
        titulo = 'Vuelve a cargar el formulario'
        mensaje = 'No pudimos verificar el envío. Abre nuevamente la página e inténtalo otra vez.'
    # No request: no context processors, usuario, sidebar, CSRF token ni sesión.
    html = render_to_string(f'errores/{codigo}.html', {'codigo': codigo, 'titulo': titulo, 'mensaje': mensaje})
    response = HttpResponse(html, status=codigo)
    response['Cache-Control'] = 'no-store'
    return response


def bad_request(request, exception):
    return respuesta_error(400)


def permission_denied(request, exception):
    return respuesta_error(403)


def page_not_found(request, exception):
    return respuesta_error(404)


def server_error(request):
    # Django conserva el registro y la notificación de la excepción original.
    return respuesta_error(500)


def csrf_failure(request, reason=''):
    return respuesta_error(403, csrf=True)
