from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory

from .models import Especie


class RecepcionForm(forms.Form):
    fecha_hora_recepcion = forms.DateTimeField(
        label="Fecha y hora de recepción",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    observaciones = forms.CharField(
        required=False,
        widget=forms.Textarea,
        label="Observaciones",
    )


class OrigenRecepcionForm(forms.Form):
    folio_origen = forms.CharField(max_length=50, label="Folio de origen Sernapesca")
    tipo_origen = forms.CharField(max_length=80, required=False, label="Tipo de origen")
    codigo_agente = forms.CharField(max_length=50, required=False, label="Código agente")
    proveedor = forms.CharField(max_length=150, required=False, label="Proveedor")


class EspecieRecibidaForm(forms.Form):
    especie = forms.ModelChoiceField(
        queryset=Especie.objects.filter(activo=True),
        label="Especie",
    )
    peso_origen_kg = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Peso de origen (kg)",
    )
    peso_recepcion_kg = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Peso en recepción (kg)",
    )


class BaseEspeciesRecibidasFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(
            form.cleaned_data and not form.cleaned_data.get("DELETE", False)
            for form in self.forms
        ):
            raise forms.ValidationError(
                "Debe ingresar al menos una especie recibida."
            )


EspecieRecibidaFormSet = formset_factory(
    EspecieRecibidaForm,
    formset=BaseEspeciesRecibidasFormSet,
    extra=1,
    can_delete=True,
)
