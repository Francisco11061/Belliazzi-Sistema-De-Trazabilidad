from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.usuarios.permisos import jefe_o_encargada_requerido

from .forms import EspecieRecibidaFormSet, OrigenRecepcionForm, RecepcionFiltroForm, RecepcionForm
from .models import Recepcion
from .services import registrar_recepcion


@jefe_o_encargada_requerido
def lista_recepciones(request):
    recepciones = Recepcion.objects.select_related("registrado_por").prefetch_related(
        "detalles__especie",
        "detalles__origen_sernapesca",
    )
    filtro = RecepcionFiltroForm(request.GET)
    filtros_activos = any(request.GET.get(campo, "").strip() for campo in filtro.fields)
    if filtro.is_valid():
        datos = filtro.cleaned_data
        q = datos["q"]
        if q:
            busqueda = Q(detalles__origen_sernapesca__folio_origen__icontains=q) | Q(
                detalles__origen_sernapesca__proveedor__icontains=q
            )
            if q.isdecimal():
                try:
                    pk = int(q)
                except ValueError:
                    pass  # Una cadena numérica excesiva sigue siendo texto de búsqueda.
                else:
                    busqueda |= Q(pk=pk)
            recepciones = recepciones.filter(busqueda)
        if datos["especie"]:
            recepciones = recepciones.filter(detalles__especie=datos["especie"])
        if datos["fecha_desde"]:
            inicio = timezone.make_aware(datetime.combine(datos["fecha_desde"], time.min))
            recepciones = recepciones.filter(fecha_hora_recepcion__gte=inicio)
        if datos["fecha_hasta"]:
            fin = timezone.make_aware(datetime.combine(datos["fecha_hasta"], time.max))
            recepciones = recepciones.filter(fecha_hora_recepcion__lte=fin)
        if datos["registrado_por"]:
            recepciones = recepciones.filter(registrado_por=datos["registrado_por"])
    else:
        recepciones = recepciones.none()

    recepciones = recepciones.distinct().order_by("-fecha_hora_recepcion", "-pk")
    page_obj = Paginator(recepciones, 10).get_page(request.GET.get("page"))
    parametros = request.GET.copy()
    parametros.pop("page", None)

    def enlace_pagina(numero):
        consulta = parametros.copy()
        consulta["page"] = numero
        return "?" + consulta.urlencode()

    return render(
        request,
        "trazabilidad/recepciones/lista.html",
        {
            "filtro": filtro,
            "filtros_activos": filtros_activos,
            "page_obj": page_obj,
            "recepciones": page_obj.object_list,
            "pagina_anterior": enlace_pagina(page_obj.previous_page_number()) if page_obj.has_previous() else None,
            "pagina_siguiente": enlace_pagina(page_obj.next_page_number()) if page_obj.has_next() else None,
        },
    )


@jefe_o_encargada_requerido
def nueva_recepcion(request):
    if request.method == "POST":
        form = RecepcionForm(request.POST)
        origen = OrigenRecepcionForm(request.POST)
        especies = EspecieRecibidaFormSet(request.POST, prefix="especies")
        form_valido = form.is_valid()
        origen_valido = origen.is_valid()
        especies_validas = especies.is_valid()
        if form_valido and origen_valido and especies_validas:
            datos_origen = {
                campo: origen.cleaned_data[campo]
                for campo in ("folio_origen", "tipo_origen", "codigo_agente", "proveedor")
            }
            datos_detalles = [
                {
                    "especie": detalle.cleaned_data["especie"],
                    "peso_origen_kg": detalle.cleaned_data["peso_origen_kg"],
                    "peso_recepcion_kg": detalle.cleaned_data["peso_recepcion_kg"],
                }
                for detalle in especies
                if detalle.cleaned_data and not detalle.cleaned_data.get("DELETE", False)
            ]
            try:
                recepcion = registrar_recepcion(
                    fecha_hora_recepcion=form.cleaned_data["fecha_hora_recepcion"],
                    registrado_por=request.user,
                    origen=datos_origen,
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
        origen = OrigenRecepcionForm()
        especies = EspecieRecibidaFormSet(prefix="especies")

    return render(
        request,
        "trazabilidad/recepciones/nueva.html",
        {"form": form, "origen": origen, "especies": especies},
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
    # Use the prefetched details; preserve all origins on historical receptions.
    detalles = list(recepcion.detalles.all())
    origenes = list({
        detalle.origen_sernapesca_id: detalle.origen_sernapesca
        for detalle in detalles
    }.values())
    return render(
        request,
        "trazabilidad/recepciones/detalle.html",
        {"recepcion": recepcion, "origenes": origenes},
    )
