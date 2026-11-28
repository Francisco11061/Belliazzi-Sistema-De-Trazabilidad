from django import forms
from datetime import date
from decimal import Decimal
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


def _sin_ceros(valor):
    """48.00 → 48 y 1.50 → 1.5, para que el campo no muestre decimales sobrantes."""
    return valor.to_integral_value() if valor == valor.to_integral_value() else valor.normalize()


class UmbralFrioForm(forms.ModelForm):
    """El tiempo recomendado se guarda en horas, pero se puede escribir en horas o en días."""

    UNIDADES = (("horas", "horas"), ("dias", "días"))
    unidad = forms.ChoiceField(label="Unidad", choices=UNIDADES, required=False)

    class Meta:
        model = UnidadFrio
        fields = ["umbral_alerta_horas"]
        labels = {"umbral_alerta_horas": "Tiempo recomendado"}
        widgets = {"umbral_alerta_horas": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        horas = self.instance.umbral_alerta_horas
        if not self.is_bound and horas is not None:
            en_dias = horas % 24 == 0
            self.initial["umbral_alerta_horas"] = _sin_ceros(horas / 24 if en_dias else horas)
            self.initial["unidad"] = "dias" if en_dias else "horas"
        else:
            # Lo que se guarda por días (mantención, almacenamiento) propone días; el túnel, horas.
            por_dias = self.instance.tipo != UnidadFrio.TipoUnidad.TUNEL_CONGELADO
            self.initial.setdefault("unidad", "dias" if por_dias else "horas")

    def clean(self):
        datos = super().clean()
        valor = datos.get("umbral_alerta_horas")
        if valor is not None and datos.get("unidad") == "dias":
            datos["umbral_alerta_horas"] = (valor * 24).quantize(Decimal("0.01"))
        return datos


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
