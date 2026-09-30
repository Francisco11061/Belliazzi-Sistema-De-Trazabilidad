from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import override
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .forms import CambioContrasenaForm, ContrasenaTemporalForm, CrearUsuarioForm, RolUsuarioForm
from .models import Rol, Usuario
from .permisos import jefe_requerido
from . import services


class LoginUsuarioView(LoginView):
    template_name = "usuarios/login.html"
    redirect_authenticated_user = True
    next_page = reverse_lazy("usuarios:inicio")

    def get_success_url(self):
        if self.request.user.cambio_contrasena_pendiente and not self.request.user.is_superuser:
            return reverse_lazy("usuarios:cambiar_contrasena")
        return super().get_success_url()


class LogoutUsuarioView(LogoutView):
    next_page = reverse_lazy("usuarios:login")


@login_required(login_url="usuarios:login")
def inicio(request):
    return render(request, "usuarios/inicio.html")


@jefe_requerido
def lista_usuarios(request):
    usuarios = Usuario.objects.select_related("rol").order_by("username", "pk")
    texto = request.GET.get("q", "").strip()
    rol = request.GET.get("rol", "")
    estado = request.GET.get("estado", "")
    if texto:
        usuarios = usuarios.filter(Q(username__icontains=texto) | Q(first_name__icontains=texto)
                                   | Q(last_name__icontains=texto))
    if rol:
        usuarios = usuarios.filter(rol__codigo=rol)
    if estado in ("activo", "inactivo"):
        usuarios = usuarios.filter(is_active=estado == "activo")
    pagina = Paginator(usuarios, 20).get_page(request.GET.get("page"))
    parametros = request.GET.copy()
    parametros.pop("page", None)
    return render(request, "usuarios/gestion/lista.html", {
        "pagina": pagina, "roles": Rol.objects.all(), "texto": texto,
        "rol_filtro": rol, "estado": estado, "filtros": parametros.urlencode(),
    })


@jefe_requerido
def detalle_usuario(request, pk):
    usuario = get_object_or_404(Usuario.objects.select_related("rol", "creado_por"), pk=pk)
    return render(request, "usuarios/gestion/detalle.html", {
        "cuenta": usuario,
        "eventos": usuario.eventos.select_related("actor"),
        "gestionable": not usuario.is_superuser and not usuario.is_staff,
    })


def _error_formulario(form, error):
    # Los errores de modelo pueden mencionar campos que no están en este formulario.
    for mensaje in error.messages:
        form.add_error(None, mensaje)


@sensitive_post_parameters("password1", "password2")
@jefe_requerido
def crear_usuario(request):
    with override("es"):
        form = CrearUsuarioForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                usuario = services.crear_usuario(
                    actor=request.user, username=data["username"], first_name=data["first_name"],
                    last_name=data["last_name"], rol=data["rol"], password=data["password1"],
                )
            except ValidationError as error:
                _error_formulario(form, error)
            else:
                messages.success(request, "Usuario creado correctamente. Debe cambiar su contraseña al iniciar sesión.")
                return redirect("usuarios:detalle_usuario", pk=usuario.pk)
        return render(request, "usuarios/gestion/formulario.html", {
            "form": form, "titulo": "Crear usuario", "boton": "Crear usuario",
            "descripcion": "La cuenta usará una contraseña temporal que deberá cambiar en su primer ingreso.",
        })


@sensitive_post_parameters("password1", "password2")
@jefe_requerido
def editar_usuario(request, pk, accion):
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario.is_superuser or usuario.is_staff:
        messages.error(request, "Las cuentas técnicas se administran desde Django Admin.")
        return redirect("usuarios:detalle_usuario", pk=pk)
    if accion == "reset" and usuario.pk == request.user.pk:
        return redirect("usuarios:cambiar_contrasena")
    with override("es"):
        if accion == "rol":
            form = RolUsuarioForm(request.POST or None, initial={"rol": usuario.rol_id})
            titulo, exito = "Editar rol", "Rol actualizado correctamente."
        else:
            form = ContrasenaTemporalForm(request.POST or None, usuario=usuario)
            titulo, exito = "Restablecer contraseña", "Contraseña restablecida correctamente."
        if request.method == "POST" and form.is_valid():
            try:
                if accion == "rol":
                    services.cambiar_rol(actor=request.user, usuario=usuario, rol=form.cleaned_data["rol"])
                else:
                    services.restablecer_contrasena(actor=request.user, usuario=usuario,
                                                   password=form.cleaned_data["password1"])
            except ValidationError as error:
                _error_formulario(form, error)
            else:
                messages.success(request, exito)
                if usuario.pk == request.user.pk:
                    return redirect("usuarios:inicio")
                return redirect("usuarios:detalle_usuario", pk=pk)
        return render(request, "usuarios/gestion/formulario.html", {
            "form": form, "titulo": titulo, "boton": "Guardar", "cuenta": usuario,
            "descripcion": ("Seleccione un rol funcional activo." if accion == "rol" else
                            "Deberá cambiar la contraseña al ingresar. Si está inactivo, seguirá inactivo."),
        })


@jefe_requerido
@require_POST
def estado_usuario(request, pk, activo):
    usuario = get_object_or_404(Usuario, pk=pk)
    try:
        services.cambiar_estado(actor=request.user, usuario=usuario, activo=activo)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Usuario reactivado correctamente." if activo else "Usuario desactivado correctamente.")
    return redirect("usuarios:detalle_usuario", pk=pk)


@sensitive_post_parameters("old_password", "new_password1", "new_password2")
@login_required(login_url="usuarios:login")
def cambiar_contrasena(request):
    with override("es"):
        form = CambioContrasenaForm(request.user, request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                usuario = services.cambiar_contrasena_propia(
                    usuario=request.user, anterior=form.cleaned_data["old_password"],
                    nueva=form.cleaned_data["new_password1"],
                )
            except ValidationError as error:
                _error_formulario(form, error)
            else:
                update_session_auth_hash(request, usuario)
                messages.success(request, "Contraseña actualizada correctamente.")
                return redirect("usuarios:inicio")
        return render(request, "usuarios/gestion/formulario.html", {
            "form": form, "titulo": "Cambiar mi contraseña", "boton": "Cambiar contraseña",
            "descripcion": "Ingrese su contraseña actual y elija una nueva para continuar.",
            "cambio_propio": True,
        })
