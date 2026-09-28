from django.urls import path
from . import views

app_name = "inventario"
urlpatterns = [
    path("", views.inventario, name="lista"),
    path("cajas/<int:pk>/ingresar/", views.ingresar, name="ingresar"),
    path("cajas/<int:pk>/mover/", views.mover, name="mover"),
]
