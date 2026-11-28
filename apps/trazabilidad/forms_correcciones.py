"""Formularios de corrección: solo los campos corregibles y el motivo obligatorio."""
from django import forms
from django.forms.models import fields_for_model

from .models import Correccion, MermaProceso

FORMATO_FECHA_HORA = "%Y-%m-%dT%H:%M:%S"


def _campo_fecha_hora(etiqueta, requerido=True):
    return forms.DateTimeField(
        label=etiqueta, required=requerido,
        input_formats=[FORMATO_FECHA_HORA, "%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}, format=FORMATO_FECHA_HORA),
    )


def _campo_kg(etiqueta):
    return forms.DecimalField(
        label=etiqueta, max_digits=10, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
    )


class CorreccionBaseForm(forms.Form):
    motivo = forms.CharField(
        max_length=Correccion._meta.get_field("motivo").max_length,
        label="¿Por qué se corrige?", widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Explica brevemente qué estaba mal."}),
        error_messages={"required": "Indica el motivo de la corrección."},
    )

    def clean(self):
        datos = super().clean()
        # El control nativo muestra hasta segundos: si la hora visible no cambió,
        # se conserva la original (con microsegundos) para no registrar un cambio falso.
        for nombre, campo in self.fields.items():
            original = self.initial.get(nombre)
            if isinstance(campo, forms.DateTimeField) and original and datos.get(nombre) == original.replace(microsecond=0):
                datos[nombre] = original
        return datos


class CorreccionDespachoForm(CorreccionBaseForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.inventario.forms import DespachoForm
        meta = DespachoForm._meta
        campos = fields_for_model(meta.model, fields=meta.fields, labels=meta.labels, widgets=meta.widgets)
        motivo = self.fields.pop("motivo")
        self.fields.update(campos)
        self.fields["motivo"] = motivo


class CorreccionEtapaForm(CorreccionBaseForm):
    fecha_hora_inicio = _campo_fecha_hora("Inicio")
    fecha_hora_termino = _campo_fecha_hora("Término", requerido=False)
    observaciones = forms.CharField(label="Observaciones", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    field_order = ["fecha_hora_inicio", "fecha_hora_termino", "observaciones", "motivo"]

    def __init__(self, *args, en_curso=False, **kwargs):
        super().__init__(*args, **kwargs)
        if en_curso:
            # Una etapa abierta se cierra con "Finalizar", no corrigiendo.
            del self.fields["fecha_hora_termino"]


class CorreccionTunelForm(CorreccionBaseForm):
    fecha_hora_ingreso = _campo_fecha_hora("Ingreso al túnel")
    fecha_hora_salida = _campo_fecha_hora("Salida del túnel", requerido=False)
    field_order = ["fecha_hora_ingreso", "fecha_hora_salida", "motivo"]

    def __init__(self, *args, en_curso=False, **kwargs):
        super().__init__(*args, **kwargs)
        if en_curso:
            del self.fields["fecha_hora_salida"]


class CorreccionPesajeForm(CorreccionBaseForm):
    peso_kg = _campo_kg("Peso postproceso (kg)")
    field_order = ["peso_kg", "motivo"]


class CorreccionMermaForm(CorreccionBaseForm):
    tipo = forms.ChoiceField(label="Tipo", choices=MermaProceso.Tipo.choices)
    cantidad_kg = _campo_kg("Cantidad (kg)")
    detalle = forms.CharField(label="Motivo de la merma", max_length=MermaProceso._meta.get_field("motivo").max_length,
                              widget=forms.Textarea(attrs={"rows": 2}))
    field_order = ["tipo", "cantidad_kg", "detalle", "motivo"]


class CorreccionLoteForm(CorreccionBaseForm):
    fecha_vencimiento = forms.DateField(label="Fecha de vencimiento", required=False,
                                        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    observaciones = forms.CharField(label="Observaciones", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    field_order = ["fecha_vencimiento", "observaciones", "motivo"]


class CorreccionCajaForm(CorreccionBaseForm):
    peso_neto_kg = _campo_kg("Peso neto real (kg)")
    field_order = ["peso_neto_kg", "motivo"]
