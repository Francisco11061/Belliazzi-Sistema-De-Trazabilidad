from django.urls import path

from . import views

app_name = "trazabilidad"

urlpatterns = [
    path("recepciones/<int:pk>/corregir/", views.corregir_recepcion, name="corregir_recepcion"),
    path("recepciones/", views.lista_recepciones, name="lista_recepciones"),
    path("recepciones/nueva/", views.nueva_recepcion, name="nueva_recepcion"),
    path("recepciones/<int:pk>/", views.detalle_recepcion, name="detalle_recepcion"),
]
