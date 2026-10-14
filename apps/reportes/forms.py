from django import forms
from datetime import date
from django.utils import timezone
from apps.trazabilidad.models import Especie, UnidadFrio


class DashboardFiltroForm(forms.Form):
    desde = forms.DateField(label="Desde", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    hasta = forms.DateField(label="Hasta", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    especie = forms.ModelChoiceField(label="Especie", queryset=Especie.objects.all().order_by("nombre"),
                                     required=False, empty_label="Todas")

    def clean(self):
        datos = super().clean()
        if datos.get("hasta") == date.max:
            self.add_error("hasta", "La fecha final está fuera del rango admitido.")
        elif datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            self.add_error("hasta", "Hasta debe ser igual o posterior a Desde. Se muestra la semana actual.")
        return datos


class UmbralFrioForm(forms.ModelForm):
    class Meta:
        model = UnidadFrio
        fields = ["umbral_alerta_horas"]
        labels = {"umbral_alerta_horas": "Umbral en horas"}
        widgets = {"umbral_alerta_horas": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}


class ReporteSernapescaForm(forms.Form):
    OPERACIONES = (("", "Todas"), ("abastecimiento", "Abastecimiento"),
                   ("produccion", "Producción"), ("destino", "Destino"))
    operacion = forms.ChoiceField(label="Tipo de operación", choices=OPERACIONES, required=False)
    fecha_inicio = forms.DateField(label="Desde", required=False, input_formats=["%Y-%m-%d"],
                                  widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    fecha_fin = forms.DateField(label="Hasta", required=False, input_formats=["%Y-%m-%d"],
                               widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))

    def clean(self):
        datos = super().clean()
        inicio = datos.get("fecha_inicio")
        fin = datos.get("fecha_fin") or timezone.localdate(timezone=timezone.get_default_timezone())
        if fin == date.max:
            self.add_error("fecha_fin", "La fecha final está fuera del rango admitido.")
        elif inicio and inicio > fin:
            self.add_error("fecha_fin", "La fecha final debe ser igual o posterior a la fecha inicial.")
        datos["fecha_fin"] = fin
        return datos
