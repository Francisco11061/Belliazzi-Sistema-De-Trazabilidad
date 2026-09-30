from django.shortcuts import redirect


class CambioContrasenaPendienteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        usuario = request.user
        if (usuario.is_authenticated and usuario.cambio_contrasena_pendiente
                and not usuario.is_superuser):
            if request.resolver_match.view_name not in {
                "usuarios:cambiar_contrasena", "usuarios:logout"
            }:
                return redirect("usuarios:cambiar_contrasena")
