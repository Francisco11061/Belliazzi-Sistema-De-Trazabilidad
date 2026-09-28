from django import forms

from apps.trazabilidad.models import Especie, LoteProduccion, UnidadFrio
from .selectors import ESTADOS, unidades_disponibles


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
