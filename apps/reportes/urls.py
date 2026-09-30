from django.urls import path
from . import views

app_name = "reportes"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("umbrales/", views.umbrales, name="umbrales"),
]
