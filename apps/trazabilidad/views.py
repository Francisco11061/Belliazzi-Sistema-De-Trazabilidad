from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.usuarios.permisos import jefe_o_encargada_requerido, jefe_requerido, personal_operativo_requerido

from .forms import EspecieRecibidaFormSet, OrigenRecepcionForm, RecepcionFiltroForm, RecepcionForm
from .models import Correccion, PartidaProceso, Recepcion
from .services import registrar_recepcion, corregir_recepcion as corregir_recepcion_service
from .forms import EspecieCorreccionFormSet, MotivoCorreccionForm, RecepcionCorreccionForm
from .forms import CantidadPartidaForm, EnviarMantencionForm, PartidaFiltroForm
from .selectors import partidas_con_situacion, presentar_partida, recepcion_con_actividad
from .services import enviar_a_mantencion, iniciar_procesamiento, retirar_de_mantencion


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
    historial = Correccion.objects.filter(
        Q(entidad_afectada="Recepcion", identificador_registro=str(recepcion.pk))
        | Q(entidad_afectada="OrigenSernapesca", identificador_registro__in=[str(o.pk) for o in origenes])
        | Q(entidad_afectada="DetalleRecepcion", identificador_registro__in=[str(d.pk) for d in detalles])
    ).select_related("usuario").order_by("-fecha_hora", "-pk")
    etiquetas = {
        "fecha_hora_recepcion": "Fecha y hora de recepción", "observaciones": "Observaciones",
        "folio_origen": "Folio de origen Sernapesca", "tipo_origen": "Tipo de origen",
        "codigo_agente": "Código agente", "proveedor": "Proveedor", "especie": "Especie",
        "peso_origen_kg": "Peso de origen", "peso_recepcion_kg": "Peso en recepción",
    }
    for entrada in historial:
        entrada.campo_visible = etiquetas.get(entrada.campo, "Campo corregido")
    return render(
        request,
        "trazabilidad/recepciones/detalle.html",
        {"recepcion": recepcion, "origenes": origenes, "historial": historial},
    )


@jefe_requerido
def corregir_recepcion(request, pk):
    recepcion = get_object_or_404(
        Recepcion.objects.prefetch_related("detalles__especie", "detalles__origen_sernapesca"), pk=pk,
    )
    detalles = sorted(recepcion.detalles.all(), key=lambda detalle: detalle.pk)
    origenes = {detalle.origen_sernapesca_id for detalle in detalles}
    if len(origenes) != 1:
        return render(request, "trazabilidad/recepciones/corregir.html", {
            "recepcion": recepcion,
            "error_estructura": (
                "Esta recepción utiliza una estructura histórica que no puede corregirse "
                "desde este formulario simplificado."
            ),
        })
    origen_actual = detalles[0].origen_sernapesca
    procesada = recepcion_con_actividad(recepcion)
    datos = request.POST if request.method == "POST" else None
    form = RecepcionCorreccionForm(datos, initial={
        "fecha_hora_recepcion": timezone.localtime(recepcion.fecha_hora_recepcion),
        "observaciones": recepcion.observaciones,
    })
    origen = OrigenRecepcionForm(datos, initial={
        campo: getattr(origen_actual, campo)
        for campo in ("folio_origen", "tipo_origen", "codigo_agente", "proveedor")
    })
    especies = EspecieCorreccionFormSet(
        datos, prefix="especies", detalle_ids=[d.pk for d in detalles],
        initial=[{
            "detalle_id": d.pk, "especie": d.especie_id,
            "peso_origen_kg": d.peso_origen_kg, "peso_recepcion_kg": d.peso_recepcion_kg,
        } for d in detalles],
        form_kwargs={"bloqueada": procesada},
    )
    motivo = MotivoCorreccionForm(datos)
    if request.method == "POST":
        validos = [form.is_valid(), origen.is_valid(), especies.is_valid(), motivo.is_valid()]
        if all(validos):
            try:
                corregir_recepcion_service(
                    recepcion=recepcion, usuario=request.user,
                    fecha_hora_recepcion=form.cleaned_data["fecha_hora_recepcion"],
                    observaciones=form.cleaned_data["observaciones"],
                    origen=origen.cleaned_data,
                    detalles=[especie.cleaned_data for especie in especies],
                    motivo=motivo.cleaned_data["motivo"],
                )
            except ValidationError as error:
                form.add_error(None, error.messages)
            else:
                messages.success(request, "Recepción corregida correctamente.")
                return redirect("trazabilidad:detalle_recepcion", pk=recepcion.pk)
    return render(request, "trazabilidad/recepciones/corregir.html", {
        "recepcion": recepcion, "form": form, "origen": origen, "especies": especies,
        "motivo": motivo, "procesada": procesada,
    })


@personal_operativo_requerido
def lista_partidas(request):
    partidas = partidas_con_situacion().filter(estado=PartidaProceso.Estado.ACTIVA, tiene_hijas=False)
    filtro = PartidaFiltroForm(request.GET)
    if filtro.is_valid():
        for campo, lookup in (
            ("partida", "pk"), ("recepcion", "detalle_recepcion__recepcion_id"),
            ("especie", "detalle_recepcion__especie"), ("situacion", "situacion"),
        ):
            if filtro.cleaned_data[campo]:
                partidas = partidas.filter(**{lookup: filtro.cleaned_data[campo]})
    else:
        partidas = partidas.none()
    pagina = Paginator(partidas.order_by("-creado_en", "-pk"), 10).get_page(request.GET.get("page"))
    pagina.object_list = [presentar_partida(p) for p in pagina.object_list]

    def enlace(numero):
        parametros = request.GET.copy()
        parametros["page"] = numero
        return "?" + parametros.urlencode()

    return render(request, "trazabilidad/partidas/lista.html", {
        "filtro": filtro, "page_obj": pagina,
        "pagina_anterior": enlace(pagina.previous_page_number()) if pagina.has_previous() else None,
        "pagina_siguiente": enlace(pagina.next_page_number()) if pagina.has_next() else None,
    })


@personal_operativo_requerido
def detalle_partida(request, pk):
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    return render(request, "trazabilidad/partidas/detalle.html", {
        "partida": partida,
        "hijas": partida.subpartidas.order_by("pk"),
        "estancias": partida.estancias_frio.select_related("unidad_frio", "ingresado_por", "retirado_por").order_by("fecha_hora_ingreso", "pk"),
        "eventos": partida.eventos.select_related("tipo_proceso", "iniciado_por").order_by("fecha_hora_inicio", "pk"),
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def operar_partida(request, pk, operacion):
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    opciones = {
        "mantencion": ("Enviar a cámara", "DISPONIBLE", EnviarMantencionForm, enviar_a_mantencion,
                       "Partida enviada a cámara de mantención correctamente."),
        "retirar": ("Retirar de cámara", "EN_MANTENCION", CantidadPartidaForm, retirar_de_mantencion,
                    "Partida retirada de la cámara de mantención correctamente."),
        "procesar": ("Iniciar procesamiento", "DISPONIBLE", CantidadPartidaForm, iniciar_procesamiento,
                     "Procesamiento iniciado correctamente."),
    }
    titulo, situacion, clase, servicio, mensaje = opciones[operacion]
    form = clase(request.POST if request.method == "POST" else None, partida=partida)
    compatible = partida.situacion == situacion
    if request.method == "POST" and form.is_valid():
        try:
            seleccionada, restante = servicio(partida=partida, usuario=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            if restante:
                destino = "en cámara" if operacion == "retirar" else "disponibles"
                cantidad = format(seleccionada.cantidad_inicial_kg, ".2f").replace(".", ",")
                resto = format(restante.cantidad_inicial_kg, ".2f").replace(".", ",")
                mensaje += (
                    f" Cantidad: {cantidad} kg; quedaron {resto} kg {destino}."
                )
            messages.success(request, mensaje)
            return redirect("trazabilidad:detalle_partida", pk=seleccionada.pk)
    return render(request, "trazabilidad/partidas/operar.html", {
        "partida": partida, "form": form, "titulo": titulo, "compatible": compatible,
    })
