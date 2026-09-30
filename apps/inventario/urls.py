from django.urls import path
from . import views

app_name = "inventario"
urlpatterns = [
    path("bajas/", views.lista_bajas, name="lista_bajas"),
    path("cajas/<int:pk>/baja/", views.crear_baja, name="crear_baja"),
    path("despachos/", views.lista_despachos, name="lista_despachos"),
    path("despachos/crear/", views.crear_despacho, name="crear_despacho"),
    path("despachos/<int:pk>/", views.detalle_despacho, name="detalle_despacho"),
    path("", views.inventario, name="lista"),
    path("cajas/<int:pk>/ingresar/", views.ingresar, name="ingresar"),
    path("cajas/<int:pk>/mover/", views.mover, name="mover"),
]
