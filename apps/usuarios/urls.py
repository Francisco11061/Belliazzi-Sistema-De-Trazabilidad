from django.urls import path

from .views import LoginUsuarioView, LogoutUsuarioView, inicio

app_name = "usuarios"

urlpatterns = [
    path("", inicio, name="inicio"),
    path("login/", LoginUsuarioView.as_view(), name="login"),
    path("logout/", LogoutUsuarioView.as_view(), name="logout"),
]
