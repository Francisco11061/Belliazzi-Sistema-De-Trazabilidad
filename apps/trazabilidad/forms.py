from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.forms import BaseFormSet, formset_factory
from django.db.models import Prefetch
from django.utils import timezone

from .formato import formatear_kg
from .models import Correccion, Especie, UnidadFrio, RutaProceso, EtapaRutaProceso, Pesaje, MermaProceso
from .selectors import SITUACIONES
from .models import LoteProduccion, ConsumoLote
from .selectors import partidas_para_lote
from .models import PresentacionBolsa
from .constantes import CAMPOS_EMBARCACION, ORIGEN_COMERCIALIZADORA, PESO_MAXIMO_CAJA_KG, TIPOS_ORIGEN


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
    tipo_comercializadora = ORIGEN_COMERCIALIZADORA

    folio_origen = forms.CharField(
        max_length=50, strip=True, label="Folio de origen Sernapesca",
        error_messages={"required": "El folio de origen Sernapesca es obligatorio."},
    )
    tipo_origen = forms.CharField(max_length=80, required=False, label="Tipo de origen", widget=forms.Select)
    codigo_agente = forms.CharField(max_length=50, required=False, label="Código agente de origen")
    proveedor = forms.CharField(max_length=150, required=False, label="Proveedor")
    nombre_embarcacion = forms.CharField(
        max_length=150, required=False, label="Nombre de la embarcación",
        widget=forms.TextInput(attrs={"list": "embarcaciones-guardadas", "autocomplete": "off"}),
    )
    matricula_embarcacion = forms.CharField(max_length=50, required=False, label="Matrícula")
    rpa_embarcacion = forms.CharField(max_length=50, required=False, label="RPA de la embarcación")
    caleta_desembarque = forms.CharField(max_length=150, required=False, label="Caleta de desembarque")
    fecha_desembarque = forms.DateField(
        required=False, label="Fecha de desembarque", input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tipo = self["tipo_origen"].value()
        opciones = [("", "Selecciona el tipo de origen"), *((t, t) for t in TIPOS_ORIGEN)]
        if tipo and tipo not in TIPOS_ORIGEN:
            opciones.append((tipo, tipo))
        self.fields["tipo_origen"].widget.choices = opciones

    @property
    def campos_origen(self):
        return (self[nombre] for nombre in self.fields if nombre not in (*CAMPOS_EMBARCACION, "proveedor"))

    @property
    def mostrar_proveedor(self):
        return self["tipo_origen"].value() == self.tipo_comercializadora

    def clean_proveedor(self):
        tipo = self.cleaned_data.get("tipo_origen")
        if tipo != self.tipo_comercializadora and self.add_prefix("proveedor") not in self.data:
            if tipo == self.initial.get("tipo_origen"):
                return self.initial.get("proveedor", "")
        return self.cleaned_data["proveedor"]

    @property
    def campos_embarcacion(self):
        return (self[nombre] for nombre in CAMPOS_EMBARCACION)


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
        label="¿Por qué se corrige?", widget=forms.Textarea(attrs={"rows": 3}),
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
    q = forms.IntegerField(required=False, min_value=1, max_value=9223372036854775807,
                           label="N.º de seguimiento o recepción",
                           widget=forms.NumberInput(attrs={"placeholder": "Ej. 125", "inputmode": "numeric"}))
    partida = forms.IntegerField(required=False, min_value=1, max_value=9223372036854775807, label="ID de seguimiento")
    recepcion = forms.IntegerField(required=False, min_value=1, max_value=9223372036854775807, label="ID de recepción")
    especie = forms.ModelChoiceField(
        queryset=Especie.objects.all(), required=False, empty_label="Todas las especies", label="Especie",
    )
    situacion = forms.ChoiceField(
        required=False, choices=[("", "Todas las situaciones"), *SITUACIONES,
            ("NO_OPERABLE", "Requiere revisión"), ("DIVIDIDA", "Dividida"),
            ("CONSUMIDA", "Consumida"), ("CERRADA", "Cerrada")], label="Situación",
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
            raise forms.ValidationError("La cantidad no puede superar la cantidad de este seguimiento.")
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


class RutaHabitualChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.nombre + " — " + " → ".join(e.tipo_proceso.nombre for e in obj.etapas.all())


class SeleccionRutaMixin:
    def configurar_rutas(self, partida):
        rutas = RutaProceso.objects.filter(
            activo=True, especie_id=partida.detalle_recepcion.especie_id,
        ).select_related("especie").prefetch_related(
            Prefetch("etapas", queryset=EtapaRutaProceso.objects.select_related("tipo_proceso")))
        from .rutas import validar_etapas_ruta
        opciones = []
        for ruta in rutas:
            try:
                validar_etapas_ruta(list(ruta.etapas.all()))
            except forms.ValidationError:
                continue
            opciones.append(ruta)
        self.fields["ruta"].queryset = rutas.filter(pk__in=[r.pk for r in opciones])
        predeterminadas = [ruta for ruta in opciones if ruta.predeterminada]
        if len(predeterminadas) == 1:
            self.fields["ruta"].initial = predeterminadas[0].pk
        elif not predeterminadas and len(opciones) == 1:
            self.fields["ruta"].initial = opciones[0].pk
        elif len(predeterminadas) > 1:
            self.fields["ruta"].help_text = "Hay varias rutas predeterminadas. La configuración es ambigua; seleccione una ruta."
        if not opciones:
            self.fields["ruta"].help_text = "No hay rutas activas para esta especie. Solicite su configuración en Administración."


class RutaPartidaForm(SeleccionRutaMixin, forms.Form):
    ruta = RutaHabitualChoice(queryset=RutaProceso.objects.none(), label="Ruta de procesamiento")

    def __init__(self, *args, partida, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurar_rutas(partida)


class IniciarProcesamientoForm(SeleccionRutaMixin, CantidadPartidaForm):
    ruta = RutaHabitualChoice(queryset=RutaProceso.objects.none(), label="Ruta de procesamiento")

    def __init__(self, *args, partida, **kwargs):
        super().__init__(*args, partida=partida, **kwargs)
        self.configurar_rutas(partida)


class TunelPartidaForm(forms.Form):
    unidad = forms.ModelChoiceField(
        queryset=UnidadFrio.objects.filter(activo=True, tipo=UnidadFrio.TipoUnidad.TUNEL_CONGELADO),
        label="Unidad de congelado",
    )


class PesoPostprocesoForm(forms.Form):
    peso_kg = Pesaje._meta.get_field("peso_kg").formfield(label="Peso postproceso (kg)", min_value=Decimal("0.01"))


class MermaProcesoForm(forms.Form):
    tipo = forms.ChoiceField(choices=MermaProceso.Tipo.choices, initial=MermaProceso.Tipo.MERMA, label="Tipo")
    cantidad_kg = MermaProceso._meta.get_field("cantidad_kg").formfield(label="Cantidad (kg)", min_value=Decimal("0.01"))
    motivo = forms.CharField(
        max_length=MermaProceso._meta.get_field("motivo").max_length, strip=True,
        label="Motivo", widget=forms.Textarea(attrs={"rows": 3}),
    )


class CantidadLoteForm(forms.Form):
    cantidad_kg = ConsumoLote._meta.get_field("cantidad_kg_utilizada").formfield(
        label="Cantidad a asignar (kg)", min_value=Decimal("0.01"),
    )


class CrearLoteForm(CantidadLoteForm):
    fecha_elaboracion = forms.DateField(label="Fecha de elaboración", widget=forms.DateInput(
        format="%Y-%m-%d", attrs={"type": "date"},
    ))
    fecha_vencimiento = forms.DateField(required=False, label="Fecha de vencimiento", widget=forms.DateInput(
        format="%Y-%m-%d", attrs={"type": "date"},
    ), help_text="Ingresa la fecha cuando esté definida. No se calcula automáticamente.")
    observaciones = forms.CharField(required=False, label="Observaciones", widget=forms.Textarea(attrs={"rows": 3}))
    field_order = ["fecha_elaboracion", "fecha_vencimiento", "cantidad_kg", "observaciones"]

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("fecha_elaboracion"), datos.get("fecha_vencimiento")
        if inicio and fin and fin < inicio:
            self.add_error("fecha_vencimiento", "La fecha de vencimiento no puede ser anterior a la elaboración.")
        return datos


class SeguimientoLoteChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, partida):
        disponible = partida.disponible_lote_kg
        return f"Seguimiento #{partida.pk} · {formatear_kg(disponible)} disponibles"


class AgregarConsumoLoteForm(CantidadLoteForm):
    partida = SeguimientoLoteChoiceField(queryset=partidas_para_lote(), label="Seguimiento")
    field_order = ["partida", "cantidad_kg"]

    def __init__(self, *args, lote, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["partida"].queryset = partidas_para_lote(lote)


class LoteFiltroForm(forms.Form):
    q = forms.CharField(required=False, label="Buscar por código o especie", max_length=200)


class CajaForm(forms.Form):
    peso_neto_kg = forms.DecimalField(
        label="Peso neto real de la caja (kg)", max_digits=10, decimal_places=2,
        min_value=Decimal("0.01"), max_value=PESO_MAXIMO_CAJA_KG,
        help_text="Ingrese el peso obtenido al pesar la caja terminada.",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )


class ComposicionCajaForm(forms.Form):
    presentacion = forms.ModelChoiceField(queryset=PresentacionBolsa.objects.filter(activo=True), label="Presentación")
    cantidad = forms.IntegerField(min_value=1, max_value=2147483647, label="Cantidad de bolsas",
                                  widget=forms.NumberInput(attrs={"step": "1"}))


class BaseComposicionCajaFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        filas = [form.cleaned_data for form in self.forms
                 if form.cleaned_data and not form.cleaned_data.get("DELETE", False)]
        if not filas:
            raise forms.ValidationError("Agrega al menos una presentación a la caja.")
        vistas = set()
        for datos in filas:
            pk = datos["presentacion"].pk
            if pk in vistas:
                raise forms.ValidationError("No repitas una presentación en la misma caja; consolida la cantidad de bolsas.")
            vistas.add(pk)
        teorico = sum((datos["cantidad"] * datos["presentacion"].peso_nominal_kg for datos in filas), Decimal("0"))
        if teorico > PESO_MAXIMO_CAJA_KG:
            raise forms.ValidationError(f"El peso teórico de la composición no puede superar {formatear_kg(PESO_MAXIMO_CAJA_KG)}.")


ComposicionCajaFormSet = formset_factory(
    ComposicionCajaForm, formset=BaseComposicionCajaFormSet, extra=2, can_delete=True,
    max_num=50, validate_max=True, absolute_max=100,
)


class CajaFiltroForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label="Buscar por ID de caja, código de lote o especie")


class SeleccionarLoteCajaForm(forms.Form):
    lote = forms.ChoiceField(label="Lote para registrar la caja")

    def __init__(self, *args, lotes, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lote"].choices = [("", "Selecciona un lote")] + [
            (str(lote.pk), f"{lote.codigo_visible} · {lote.especie.nombre} · {formatear_kg(lote.disponible_empacar)} disponibles")
            for lote in lotes
        ]
