from django.urls import path

from . import views

app_name = "producto_terminado"

urlpatterns = [
    path("cajas/", views.lista_cajas, name="lista_cajas"),
    path("cajas/qr/<uuid:identificador>/", views.consulta_caja_qr, name="consulta_caja_qr"),
    path("cajas/<int:pk>/qr.png", views.caja_qr_png, name="caja_qr_png"),
    path("cajas/<int:pk>/", views.detalle_caja, name="detalle_caja"),
    path("lotes/<int:pk>/crear-caja/", views.crear_caja, name="crear_caja"),
    path("lotes/", views.lista_lotes, name="lista_lotes"),
    path("lotes/desde-seguimiento/<int:pk>/", views.crear_lote, name="crear_lote"),
    path("lotes/<int:pk>/", views.detalle_lote, name="detalle_lote"),
    path("lotes/<int:pk>/agregar/", views.agregar_producto_lote, name="agregar_producto_lote"),
]
