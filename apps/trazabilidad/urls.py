from django.urls import path

from . import views

app_name = "trazabilidad"

urlpatterns = [
    path("partidas/", views.lista_partidas, name="lista_partidas"),
    path("partidas/<int:pk>/", views.detalle_partida, name="detalle_partida"),
    path("partidas/<int:pk>/mantencion/", views.operar_partida, {"operacion": "mantencion"}, name="enviar_partida_mantencion"),
    path("partidas/<int:pk>/retirar/", views.operar_partida, {"operacion": "retirar"}, name="retirar_partida_mantencion"),
    path("partidas/<int:pk>/procesar/", views.operar_partida, {"operacion": "procesar"}, name="procesar_partida"),
    path("recepciones/<int:pk>/corregir/", views.corregir_recepcion, name="corregir_recepcion"),
    path("recepciones/", views.lista_recepciones, name="lista_recepciones"),
    path("recepciones/nueva/", views.nueva_recepcion, name="nueva_recepcion"),
    path("recepciones/<int:pk>/", views.detalle_recepcion, name="detalle_recepcion"),
]
