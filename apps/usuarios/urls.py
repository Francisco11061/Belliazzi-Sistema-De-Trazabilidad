from django.urls import path

from .views import LoginUsuarioView, LogoutUsuarioView, inicio
from . import views

app_name = "usuarios"

urlpatterns = [
    path("", inicio, name="inicio"),
    path("login/", LoginUsuarioView.as_view(), name="login"),
    path("logout/", LogoutUsuarioView.as_view(), name="logout"),
    path("mi-contrasena/", views.cambiar_contrasena, name="cambiar_contrasena"),
    path("usuarios/", views.lista_usuarios, name="lista_usuarios"),
    path("usuarios/crear/", views.crear_usuario, name="crear_usuario"),
    path("usuarios/<int:pk>/", views.detalle_usuario, name="detalle_usuario"),
    path("usuarios/<int:pk>/rol/", views.editar_usuario, {"accion": "rol"}, name="editar_rol"),
    path("usuarios/<int:pk>/contrasena/", views.editar_usuario, {"accion": "reset"}, name="reset_contrasena"),
    path("usuarios/<int:pk>/desactivar/", views.estado_usuario, {"activo": False}, name="desactivar_usuario"),
    path("usuarios/<int:pk>/reactivar/", views.estado_usuario, {"activo": True}, name="reactivar_usuario"),
]
