from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


ROL_JEFE = "JEFE"
ROL_ENCARGADA = "ENCARGADA"
ROL_OPERARIA = "OPERARIA"


def tiene_rol(usuario, *codigos_roles):
    if not usuario.is_authenticated:
        return False
    if usuario.is_superuser:
        return True
    if usuario.rol is None:
        return False
    return usuario.rol.codigo in codigos_roles


def roles_requeridos(*codigos_roles):
    def decorador(vista):
        @login_required(login_url="usuarios:login")
        @wraps(vista)
        def vista_protegida(request, *args, **kwargs):
            if not tiene_rol(request.user, *codigos_roles):
                raise PermissionDenied
            return vista(request, *args, **kwargs)

        return vista_protegida

    return decorador


jefe_requerido = roles_requeridos(ROL_JEFE)

jefe_o_encargada_requerido = roles_requeridos(
    ROL_JEFE,
    ROL_ENCARGADA,
)

personal_operativo_requerido = roles_requeridos(
    ROL_JEFE,
    ROL_ENCARGADA,
    ROL_OPERARIA,
)
