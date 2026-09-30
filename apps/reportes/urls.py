from django.urls import path
from . import views

app_name = "reportes"
urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/umbrales/", views.umbrales, name="umbrales"),
    path("reportes/", views.sernapesca, name="sernapesca"),
    path("reportes/sernapesca.xlsx", views.sernapesca, {"descargar": True}, name="descargar_sernapesca"),
]
