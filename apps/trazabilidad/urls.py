from django.urls import path

from . import views, views_rutas, views_consulta

app_name = "trazabilidad"

urlpatterns = [
    path("consulta/", views_consulta.buscar, name="consulta_integral"),
    path("consulta/<str:tipo>/<int:pk>/", views_consulta.ficha, name="ficha_integral"),
    path("rutas/", views_rutas.rutas, name="rutas"),
    path("rutas/nueva/", views_rutas.editar_ruta, name="nueva_ruta"),
    path("rutas/<int:pk>/", views_rutas.editar_ruta, name="editar_ruta"),
    path("rutas/<int:pk>/duplicar/", views_rutas.editar_ruta, {"duplicar": True}, name="duplicar_ruta"),
    path("rutas/<int:pk>/estado/", views_rutas.cambiar_estado_ruta, name="estado_ruta"),
    path("operaciones/", views_rutas.operaciones, name="operaciones"),
    path("operaciones/<int:pk>/", views_rutas.operaciones, name="editar_operacion"),
    path("solicitudes-ruta/", views_rutas.solicitudes, name="solicitudes_ruta"),
    path("solicitudes-ruta/<int:pk>/", views_rutas.resolver, name="resolver_solicitud"),
    path("partidas/<int:pk>/seleccionar-operaciones/", views_rutas.seleccionar_operaciones, name="seleccionar_operaciones"),
    path("partidas/<int:pk>/peso-postproceso/", views.registrar_peso_postproceso, name="registrar_peso_postproceso"),
    path("partidas/<int:pk>/merma/", views.registrar_merma_proceso, name="registrar_merma_proceso"),
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
