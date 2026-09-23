from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory

from .models import Especie, OrigenSernapesca


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


class DetalleRecepcionForm(forms.Form):
    origen_sernapesca = forms.ModelChoiceField(
        queryset=OrigenSernapesca.objects.all(),
        label="Origen Sernapesca",
    )
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


class BaseDetalleRecepcionFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not any(
            form.cleaned_data and not form.cleaned_data.get("DELETE", False)
            for form in self.forms
        ):
            raise forms.ValidationError(
                "Debe ingresar al menos un detalle de recepción."
            )


DetalleRecepcionFormSet = formset_factory(
    DetalleRecepcionForm,
    formset=BaseDetalleRecepcionFormSet,
    extra=1,
    can_delete=True,
)
