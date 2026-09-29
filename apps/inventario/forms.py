from django import forms

from apps.trazabilidad.models import Caja, Especie, LoteProduccion, UnidadFrio
from .selectors import ESTADOS, cajas_para_despacho, unidades_disponibles
from .models import Despacho
from .despachos import validar_datos_despacho


class LoteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.codigo_visible


class DestinoForm(forms.Form):
    unidad = forms.ModelChoiceField(queryset=UnidadFrio.objects.none(), label="Cámara de destino", empty_label="Selecciona una cámara")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unidad"].queryset = unidades_disponibles()


class InventarioFiltroForm(forms.Form):
    especie = forms.ModelChoiceField(queryset=Especie.objects.all(), required=False, empty_label="Todas las especies")
    lote = LoteChoiceField(queryset=LoteProduccion.objects.all().order_by("codigo_lote"), required=False, empty_label="Todos los lotes")
    camara = forms.ModelChoiceField(queryset=UnidadFrio.objects.filter(tipo=UnidadFrio.TipoUnidad.ALMACENAMIENTO), required=False, label="Cámara", empty_label="Todas las cámaras")
    estado = forms.ChoiceField(required=False, choices=[("", "Todos los estados"), *ESTADOS])


class DespachoForm(forms.ModelForm):
    cajas = forms.ModelMultipleChoiceField(queryset=Caja.objects.none(), widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Seleccione al menos una caja.", "invalid_choice": "Una o más cajas ya no están disponibles para despacho."})

    class Meta:
        model = Despacho
        fields = ["tipo_destino", "destino", "rut_destinatario", "pais_destino", "tipo_documento", "numero_documento", "fecha_documento", "observaciones"]
        labels = {"tipo_destino": "Tipo de destino", "destino": "Destinatario / razón social",
                  "rut_destinatario": "RUT del destinatario", "pais_destino": "País de destino",
                  "tipo_documento": "Tipo de documento", "numero_documento": "Número de documento",
                  "fecha_documento": "Fecha del documento"}
        widgets = {"fecha_documento": forms.DateInput(format="%Y-%m-%d", attrs={"type":"date"}),
                   "tipo_documento": forms.TextInput(attrs={"list":"tipos-documento", "placeholder":"Ej.: Guía de despacho"}),
                   "observaciones": forms.Textarea(attrs={"rows":3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cajas"].queryset = cajas_para_despacho()

    def clean_cajas(self):
        valores = self.fields["cajas"].widget.value_from_datadict(self.data, self.files, self.add_prefix("cajas"))
        if len(valores) != len({int(pk) for pk in valores}):
            raise forms.ValidationError("No puede seleccionar una caja más de una vez.")
        return self.cleaned_data["cajas"]

    def clean(self):
        datos = super().clean()
        return validar_datos_despacho(datos)


class DespachoFiltroForm(forms.Form):
    destinatario = forms.CharField(required=False, max_length=200, label="Destinatario o país")
    tipo_destino = forms.ChoiceField(required=False, choices=[("", "Todos los destinos"), *Despacho.TipoDestino.choices])
    fecha = forms.DateField(required=False, widget=forms.DateInput(attrs={"type":"date"}))
