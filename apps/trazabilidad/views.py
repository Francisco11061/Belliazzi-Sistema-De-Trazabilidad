from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.usuarios.permisos import jefe_o_encargada_requerido

from .forms import DetalleRecepcionFormSet, RecepcionForm
from .models import Recepcion
from .services import registrar_recepcion


@jefe_o_encargada_requerido
def lista_recepciones(request):
    recepciones = Recepcion.objects.select_related("registrado_por").prefetch_related(
        "detalles__especie",
        "detalles__origen_sernapesca",
    )
    return render(
        request,
        "trazabilidad/recepciones/lista.html",
        {"recepciones": recepciones},
    )


@jefe_o_encargada_requerido
def nueva_recepcion(request):
    if request.method == "POST":
        form = RecepcionForm(request.POST)
        detalles = DetalleRecepcionFormSet(request.POST)
        form_valido = form.is_valid()
        detalles_validos = detalles.is_valid()
        if form_valido and detalles_validos:
            datos_detalles = [
                {
                    "origen_sernapesca": detalle.cleaned_data["origen_sernapesca"],
                    "especie": detalle.cleaned_data["especie"],
                    "peso_origen_kg": detalle.cleaned_data["peso_origen_kg"],
                    "peso_recepcion_kg": detalle.cleaned_data["peso_recepcion_kg"],
                }
                for detalle in detalles
                if detalle.cleaned_data and not detalle.cleaned_data.get("DELETE", False)
            ]
            try:
                recepcion = registrar_recepcion(
                    fecha_hora_recepcion=form.cleaned_data["fecha_hora_recepcion"],
                    registrado_por=request.user,
                    detalles=datos_detalles,
                    observaciones=form.cleaned_data["observaciones"],
                )
            except ValidationError as error:
                form.add_error(None, error.messages)
            else:
                messages.success(request, "Recepción registrada correctamente.")
                return redirect("trazabilidad:detalle_recepcion", pk=recepcion.pk)
    else:
        form = RecepcionForm(initial={"fecha_hora_recepcion": timezone.localtime()})
        detalles = DetalleRecepcionFormSet()

    return render(
        request,
        "trazabilidad/recepciones/nueva.html",
        {"form": form, "detalles": detalles},
    )


@jefe_o_encargada_requerido
def detalle_recepcion(request, pk):
    recepcion = get_object_or_404(
        Recepcion.objects.select_related("registrado_por").prefetch_related(
            "detalles__especie",
            "detalles__origen_sernapesca",
        ),
        pk=pk,
    )
    return render(
        request,
        "trazabilidad/recepciones/detalle.html",
        {"recepcion": recepcion},
    )
