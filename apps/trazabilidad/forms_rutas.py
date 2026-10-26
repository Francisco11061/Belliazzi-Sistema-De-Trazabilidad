from decimal import Decimal
from django import forms
from django.forms import BaseFormSet, formset_factory
from .models import Especie, RutaProceso, TipoProceso
from .services_rutas import secuencia_validada


class RutaGestionForm(forms.Form):
    nombre = forms.CharField(max_length=120)
    especie = forms.ModelChoiceField(queryset=Especie.objects.all())
    descripcion = forms.CharField(required=False, label="Descripción", widget=forms.Textarea(attrs={"rows": 2}))
    activo = forms.BooleanField(required=False, initial=True, label="Activa")
    predeterminada = forms.BooleanField(required=False, label="Predeterminada para la especie")


class OperacionGestionForm(forms.Form):
    codigo = forms.RegexField(regex=r"\A[A-Z][A-Z0-9_]*\Z", max_length=30, label="Código",
                             help_text="Código estable en mayúsculas, sin espacios.")
    nombre = forms.CharField(max_length=80)
    activo = forms.BooleanField(required=False, initial=True, label="Activa")


class OperacionSecuenciaForm(forms.Form):
    tipo_proceso = forms.ModelChoiceField(label="Operación", queryset=TipoProceso.objects.filter(activo=True).exclude(codigo="PROCESAMIENTO"))


class SecuenciaBaseFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        filas = self.ordered_forms
        ordenes = [f.cleaned_data.get("ORDER") for f in filas]
        if any(o is None or o < 1 for o in ordenes) or len(set(ordenes)) != len(ordenes):
            raise forms.ValidationError("Indica un orden positivo y distinto para cada operación.")
        secuencia_validada([f.cleaned_data["tipo_proceso"].pk for f in filas])

    def operaciones(self):
        return [f.cleaned_data["tipo_proceso"].pk for f in self.ordered_forms]


SecuenciaFormSet = formset_factory(OperacionSecuenciaForm, formset=SecuenciaBaseFormSet,
    can_order=True, can_delete=True, extra=1, max_num=100, validate_max=True, absolute_max=100)


class SeleccionOperacionesForm(forms.Form):
    cantidad_kg = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"), label="Cantidad a procesar (kg)")
    ruta = forms.ModelChoiceField(queryset=RutaProceso.objects.none(), required=False, label="Ruta autorizada coincidente")
    observacion = forms.CharField(required=False, max_length=2000, label="Observación de la solicitud", widget=forms.Textarea(attrs={"rows": 2}))


class ResolverSolicitudForm(forms.Form):
    decision = forms.ChoiceField(choices=[("aprobar", "Aprobar"), ("rechazar", "Rechazar")], label="Decisión")
    nombre = forms.CharField(required=False, max_length=120, label="Nombre de la nueva ruta")
    ruta_existente = forms.ModelChoiceField(queryset=RutaProceso.objects.none(), required=False, label="Ruta equivalente existente")
    respuesta = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))
