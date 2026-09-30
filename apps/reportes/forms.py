from django import forms
from datetime import date
from django.utils import timezone
from apps.trazabilidad.models import UnidadFrio


class UmbralFrioForm(forms.ModelForm):
    class Meta:
        model = UnidadFrio
        fields = ["umbral_alerta_horas"]
        labels = {"umbral_alerta_horas": "Umbral en horas"}
        widgets = {"umbral_alerta_horas": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}


class ReporteSernapescaForm(forms.Form):
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
