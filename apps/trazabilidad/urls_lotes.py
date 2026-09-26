from django.urls import path

from . import views

app_name = "producto_terminado"

urlpatterns = [
    path("lotes/", views.lista_lotes, name="lista_lotes"),
    path("lotes/desde-seguimiento/<int:pk>/", views.crear_lote, name="crear_lote"),
    path("lotes/<int:pk>/", views.detalle_lote, name="detalle_lote"),
    path("lotes/<int:pk>/agregar/", views.agregar_producto_lote, name="agregar_producto_lote"),
]
