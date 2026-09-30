from django import forms
from apps.trazabilidad.models import UnidadFrio


class UmbralFrioForm(forms.ModelForm):
    class Meta:
        model = UnidadFrio
        fields = ["umbral_alerta_horas"]
        labels = {"umbral_alerta_horas": "Umbral en horas"}
        widgets = {"umbral_alerta_horas": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}
