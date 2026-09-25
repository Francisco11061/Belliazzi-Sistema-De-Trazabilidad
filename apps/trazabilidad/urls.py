from django.urls import path

from . import views

app_name = "trazabilidad"

urlpatterns = [
    path("partidas/<int:pk>/procesamiento/", views.gestionar_procesamiento, name="gestionar_procesamiento"),
    path("partidas/<int:pk>/etapas/<int:etapa_pk>/iniciar/", views.operar_etapa, {"accion": "iniciar"}, name="iniciar_etapa"),
    path("partidas/<int:pk>/etapas/<int:etapa_pk>/finalizar/", views.operar_etapa, {"accion": "finalizar"}, name="finalizar_etapa"),
    path("partidas/<int:pk>/tunel/", views.enviar_partida_tunel, name="enviar_partida_tunel"),
    path("partidas/<int:pk>/tunel/retirar/", views.retirar_partida_tunel, name="retirar_partida_tunel"),
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
