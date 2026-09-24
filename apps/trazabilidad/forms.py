from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from .models import Correccion, Especie, UnidadFrio
from .selectors import SITUACIONES


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

    def clean_fecha_hora_recepcion(self):
        fecha = self.cleaned_data["fecha_hora_recepcion"]
        if fecha > timezone.now():
            raise forms.ValidationError(
                "La recepción no puede registrarse con una fecha y hora posterior a la actual."
            )
        return fecha


class OrigenRecepcionForm(forms.Form):
    folio_origen = forms.CharField(
        max_length=50, strip=True, label="Folio de origen Sernapesca",
        error_messages={"required": "El folio de origen Sernapesca es obligatorio."},
    )
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

    def clean(self):
        datos = super().clean()
        origen = datos.get("peso_origen_kg")
        recepcion = datos.get("peso_recepcion_kg")
        if origen is not None and recepcion is not None and recepcion > origen:
            self.add_error(
                "peso_recepcion_kg",
                "El peso en recepción no puede ser mayor que el peso de origen.",
            )
        return datos


class BaseEspeciesRecibidasFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        filas = [
            form.cleaned_data for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE", False)
        ]
        if not filas:
            raise forms.ValidationError("Debe ingresar al menos una especie recibida.")
        vistas = set()
        repetidas = set()
        errores = []
        for datos in filas:
            especie = datos["especie"]
            if especie.pk in vistas and especie.pk not in repetidas:
                errores.append(
                    f"La especie '{especie.nombre}' ya fue agregada a esta recepción. "
                    "Consolide los pesos en una sola fila."
                )
                repetidas.add(especie.pk)
            vistas.add(especie.pk)
        if errores:
            raise forms.ValidationError(errores)


EspecieRecibidaFormSet = formset_factory(
    EspecieRecibidaForm,
    formset=BaseEspeciesRecibidasFormSet,
    extra=1,
    can_delete=True,
)


class RecepcionFiltroForm(forms.Form):
    q = forms.CharField(
        required=False, label="",
        widget=forms.TextInput(attrs={"placeholder": "Buscar por ID, folio o proveedor..."}),
    )
    especie = forms.ModelChoiceField(
        queryset=Especie.objects.filter(activo=True), required=False,
        empty_label="Todas las especies", label="Especie",
    )
    fecha_desde = forms.DateField(
        required=False, label="Desde",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    fecha_hasta = forms.DateField(
        required=False, label="Hasta",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    registrado_por = forms.ModelChoiceField(
        queryset=get_user_model().objects.filter(
            recepciones_registradas__isnull=False,
        ).only("pk", "username", "first_name", "last_name").distinct().order_by("username"),
        required=False, empty_label="Todos los usuarios", label="Registrado por",
    )

    def clean(self):
        datos = super().clean()
        desde = datos.get("fecha_desde")
        hasta = datos.get("fecha_hasta")
        if desde and hasta and desde > hasta:
            raise forms.ValidationError(
                "La fecha inicial no puede ser posterior a la fecha final."
            )
        return datos


class MotivoCorreccionForm(forms.Form):
    motivo = forms.CharField(
        max_length=Correccion._meta.get_field("motivo").max_length,
        label="Motivo de la corrección", widget=forms.Textarea,
        error_messages={"required": "Indica el motivo de la corrección."},
    )


class EspecieCorreccionForm(EspecieRecibidaForm):
    detalle_id = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, bloqueada=False, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in ("especie", "peso_origen_kg", "peso_recepcion_kg"):
            # Sólo bloqueo visual: los valores POST manipulados se validan
            # y llegan al service, que vuelve a comprobar el procesamiento.
            if bloqueada:
                self.fields[nombre].widget.attrs["disabled"] = True

    def full_clean(self):
        # Los controles disabled no son enviados por el navegador. Restaurar
        # sólo los valores ausentes desde initial; nunca sobrescribir un POST.
        if self.is_bound:
            datos = self.data.copy()
            for nombre in ("especie", "peso_origen_kg", "peso_recepcion_kg"):
                if self.fields[nombre].widget.attrs.get("disabled"):
                    clave = self.add_prefix(nombre)
                    if clave not in datos:
                        datos[clave] = self.initial.get(nombre)
            self.data = datos
        super().full_clean()


class BaseEspeciesCorreccionFormSet(BaseEspeciesRecibidasFormSet):
    def __init__(self, *args, detalle_ids, **kwargs):
        self.detalle_ids = set(detalle_ids)
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        enviados = [form.cleaned_data.get("detalle_id") for form in self.forms]
        if len(enviados) != len(self.detalle_ids) or set(enviados) != self.detalle_ids:
            raise forms.ValidationError(
                "Debes corregir exactamente las especies existentes de esta recepción; "
                "no se permite agregar, quitar ni repetir detalles."
            )


EspecieCorreccionFormSet = formset_factory(
    EspecieCorreccionForm, formset=BaseEspeciesCorreccionFormSet, extra=0, can_delete=False,
)


class RecepcionCorreccionForm(RecepcionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_hora_recepcion"].widget = forms.DateTimeInput(
            attrs={"type": "datetime-local", "step": "1"}, format="%Y-%m-%dT%H:%M:%S",
        )

    def clean_fecha_hora_recepcion(self):
        fecha = super().clean_fecha_hora_recepcion()
        original = self.initial.get("fecha_hora_recepcion")
        # El control nativo muestra segundos; conservar los microsegundos de
        # la BD cuando la fecha visible no fue cambiada.
        if original and fecha == original.replace(microsecond=0):
            return original
        return fecha


class PartidaFiltroForm(forms.Form):
    partida = forms.IntegerField(required=False, min_value=1, max_value=9223372036854775807, label="ID de partida")
    recepcion = forms.IntegerField(required=False, min_value=1, max_value=9223372036854775807, label="ID de recepción")
    especie = forms.ModelChoiceField(
        queryset=Especie.objects.all(), required=False, empty_label="Todas las especies", label="Especie",
    )
    situacion = forms.ChoiceField(
        required=False, choices=[("", "Todas las situaciones"), *SITUACIONES], label="Situación",
    )


class CantidadPartidaForm(forms.Form):
    cantidad_kg = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), label="Cantidad (kg)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    def __init__(self, *args, partida, **kwargs):
        super().__init__(*args, **kwargs)
        self.partida = partida
        self.fields["cantidad_kg"].initial = partida.cantidad_inicial_kg
        self.fields["cantidad_kg"].widget.attrs["max"] = str(partida.cantidad_inicial_kg)

    def clean_cantidad_kg(self):
        cantidad = self.cleaned_data["cantidad_kg"]
        if cantidad > self.partida.cantidad_inicial_kg:
            raise forms.ValidationError("La cantidad no puede superar la cantidad de la partida.")
        return cantidad


class EnviarMantencionForm(CantidadPartidaForm):
    unidad = forms.ModelChoiceField(
        queryset=UnidadFrio.objects.filter(activo=True, tipo=UnidadFrio.TipoUnidad.MANTENCION),
        label="Unidad de mantención",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        unidades = list(self.fields["unidad"].queryset[:2])
        if len(unidades) == 1:
            self.fields["unidad"].initial = unidades[0].pk
