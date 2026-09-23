from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import render
from django.urls import reverse_lazy


class LoginUsuarioView(LoginView):
    template_name = "usuarios/login.html"
    redirect_authenticated_user = True
    next_page = reverse_lazy("usuarios:inicio")


class LogoutUsuarioView(LogoutView):
    next_page = reverse_lazy("usuarios:login")


@login_required(login_url="usuarios:login")
def inicio(request):
    return render(request, "usuarios/inicio.html")
