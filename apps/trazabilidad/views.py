from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.usuarios.permisos import jefe_o_encargada_requerido, jefe_requerido, personal_operativo_requerido

from .forms import EspecieRecibidaFormSet, OrigenRecepcionForm, RecepcionFiltroForm, RecepcionForm
from .models import Correccion, EtapaRutaProceso, PartidaProceso, Recepcion
from .services import registrar_recepcion, corregir_recepcion as corregir_recepcion_service
from .forms import EspecieCorreccionFormSet, MotivoCorreccionForm, RecepcionCorreccionForm
from .forms import CantidadPartidaForm, EnviarMantencionForm, PartidaFiltroForm
from .selectors import partidas_con_situacion, presentar_partida, recepcion_con_actividad
from .services import enviar_a_mantencion, iniciar_procesamiento, retirar_de_mantencion
from .forms import IniciarProcesamientoForm, RutaPartidaForm, TunelPartidaForm
from .selectors import resumen_procesamiento
from .services import (
    asignar_ruta_proceso, enviar_a_tunel, finalizar_etapa_proceso,
    iniciar_etapa_proceso, retirar_de_tunel,
)
from .forms import PesoPostprocesoForm, MermaProcesoForm
from .forms import CrearLoteForm, AgregarConsumoLoteForm, LoteFiltroForm
from .selectors import partidas_con_disponibilidad, puede_asignar_lote, lotes_con_totales
from .services import crear_lote_produccion, agregar_consumo_lote
from .forms import ComposicionCajaFormSet, CajaFiltroForm, CajaForm
from .constantes import PESO_MAXIMO_CAJA_KG
from .models import PresentacionBolsa
from .selectors import lotes_con_empaque, cajas_con_peso, puede_crear_caja
from .selectors import cajas_con_trazabilidad
from .selectors import origen_unico_lote
from .models import Caja
from .qr import generar_png_qr_caja
from apps.usuarios.permisos import tiene_rol, ROL_JEFE, ROL_ENCARGADA
from .services import crear_caja as crear_caja_service
from .selectors import resumen_pesajes_mermas, ADVERTENCIA_PESO_SUPERIOR
from .services import (
    registrar_peso_postproceso as registrar_peso_postproceso_service,
    registrar_merma_proceso as registrar_merma_proceso_service,
)


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
    partida = presentar_partida(get_object_or_404(partidas_con_disponibilidad(), pk=pk))
    return render(request, "trazabilidad/partidas/detalle.html", {
        "partida": partida,
        **resumen_pesajes_mermas(partida),
        "puede_asignar_lote": puede_asignar_lote(partida),
        "consumos_lotes": partida.consumos_lote.select_related("lote_produccion").order_by("pk"),
        "hijas": partida.subpartidas.order_by("pk"),
        "estancias": partida.estancias_frio.select_related("unidad_frio", "ingresado_por", "retirado_por").order_by("fecha_hora_ingreso", "pk"),
        "eventos": partida.eventos.select_related("tipo_proceso", "iniciado_por", "finalizado_por").order_by("fecha_hora_inicio", "pk"),
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def operar_partida(request, pk, operacion):
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    opciones = {
        "mantencion": ("Enviar a cámara", "DISPONIBLE", EnviarMantencionForm, enviar_a_mantencion,
                       "Producto enviado a cámara de mantención correctamente."),
        "retirar": ("Retirar de cámara", "EN_MANTENCION", CantidadPartidaForm, retirar_de_mantencion,
                    "Producto retirado de la cámara de mantención correctamente."),
        "procesar": ("Iniciar procesamiento", "DISPONIBLE", IniciarProcesamientoForm, iniciar_procesamiento,
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
                cantidad = format(seleccionada.cantidad_inicial_kg, ".2f").replace(".", ",")
                resto = format(restante.cantidad_inicial_kg, ".2f").replace(".", ",")
                mensaje = {
                    "mantencion": f"Se enviaron {cantidad} kg a cámara de mantención y quedaron {resto} kg disponibles.",
                    "retirar": f"Se retiraron {cantidad} kg de la cámara de mantención y quedaron {resto} kg en cámara.",
                    "procesar": f"Se enviaron {cantidad} kg a procesamiento y quedaron {resto} kg disponibles.",
                }[operacion]
            messages.success(request, mensaje)
            return redirect("trazabilidad:detalle_partida", pk=seleccionada.pk)
    return render(request, "trazabilidad/partidas/operar.html", {
        "partida": partida, "form": form, "titulo": titulo, "compatible": compatible,
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def gestionar_procesamiento(request, pk):
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    resumen = resumen_procesamiento(partida)
    form = RutaPartidaForm(request.POST if request.method == "POST" else None, partida=partida)
    if request.method == "POST" and form.is_valid():
        try:
            asignar_ruta_proceso(partida=partida, ruta=form.cleaned_data["ruta"], usuario=request.user)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Ruta de procesamiento asignada correctamente.")
            return redirect("trazabilidad:gestionar_procesamiento", pk=pk)
    return render(request, "trazabilidad/partidas/procesamiento.html", {
        "partida": partida, "form": form, **resumen,
    })


@personal_operativo_requerido
@require_http_methods(["POST"])
def operar_etapa(request, pk, etapa_pk, accion):
    partida = get_object_or_404(PartidaProceso, pk=pk)
    etapa = get_object_or_404(EtapaRutaProceso, pk=etapa_pk)
    servicio = iniciar_etapa_proceso if accion == "iniciar" else finalizar_etapa_proceso
    try:
        servicio(partida=partida, etapa=etapa, usuario=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        actual = presentar_partida(partidas_con_situacion().get(pk=pk))
        if accion == "finalizar" and resumen_procesamiento(actual)["ruta_completa"]:
            messages.success(request, "Procesamiento completado. El producto está listo para ingresar al túnel de congelado.")
        else:
            messages.success(request, "Etapa iniciada correctamente." if accion == "iniciar" else "Etapa finalizada correctamente.")
    return redirect("trazabilidad:gestionar_procesamiento", pk=pk)


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def enviar_partida_tunel(request, pk):
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    form = TunelPartidaForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            enviar_a_tunel(partida=partida, unidad=form.cleaned_data["unidad"], usuario=request.user)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Producto enviado al túnel de congelado correctamente.")
            return redirect("trazabilidad:detalle_partida", pk=pk)
    return render(request, "trazabilidad/partidas/tunel.html", {
        "partida": partida, "form": form,
        "compatible": resumen_procesamiento(partida)["puede_enviar_tunel"],
    })


@personal_operativo_requerido
@require_http_methods(["POST"])
def retirar_partida_tunel(request, pk):
    partida = get_object_or_404(PartidaProceso, pk=pk)
    try:
        retirar_de_tunel(partida=partida, usuario=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Producto retirado del túnel. Pendiente de pesaje postproceso.")
    return redirect("trazabilidad:detalle_partida", pk=pk)


def _registrar_medicion(request, pk, *, es_pesaje):
    partida = presentar_partida(get_object_or_404(partidas_con_situacion(), pk=pk))
    clase = PesoPostprocesoForm if es_pesaje else MermaProcesoForm
    servicio = registrar_peso_postproceso_service if es_pesaje else registrar_merma_proceso_service
    form = clase(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            registro = servicio(partida=partida, usuario=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Peso postproceso registrado correctamente." if es_pesaje else "Pérdida o merma registrada correctamente.")
            if es_pesaje and registro.peso_kg > registro.partida.cantidad_inicial_kg:
                messages.warning(request, ADVERTENCIA_PESO_SUPERIOR)
            return redirect("trazabilidad:detalle_partida", pk=pk)
    return render(request, "trazabilidad/partidas/registrar_medicion.html", {
        "partida": partida, "form": form, "es_pesaje": es_pesaje,
        "titulo": "Registrar peso postproceso" if es_pesaje else "Registrar pérdida o merma",
        "compatible": partida.situacion == "PENDIENTE_PESAJE" if es_pesaje else partida.puede_registrar_merma,
        **resumen_pesajes_mermas(partida),
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def registrar_peso_postproceso(request, pk):
    return _registrar_medicion(request, pk, es_pesaje=True)


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def registrar_merma_proceso(request, pk):
    return _registrar_medicion(request, pk, es_pesaje=False)


@personal_operativo_requerido
def lista_lotes(request):
    filtro = LoteFiltroForm(request.GET)
    lotes = lotes_con_empaque()
    if filtro.is_valid():
        q = filtro.cleaned_data["q"]
        if q:
            lotes = lotes.filter(Q(codigo_lote__icontains=q) | Q(especie__nombre__icontains=q))
    else:
        lotes = lotes.none()
    pagina = Paginator(lotes.order_by("-fecha_elaboracion", "-pk"), 10).get_page(request.GET.get("page"))

    def enlace(numero):
        parametros = request.GET.copy()
        parametros["page"] = numero
        return "?" + parametros.urlencode()

    return render(request, "trazabilidad/lotes/lista.html", {
        "filtro": filtro, "page_obj": pagina,
        "pagina_anterior": enlace(pagina.previous_page_number()) if pagina.has_previous() else None,
        "pagina_siguiente": enlace(pagina.next_page_number()) if pagina.has_next() else None,
    })


@personal_operativo_requerido
def detalle_lote(request, pk):
    lote = get_object_or_404(lotes_con_empaque(), pk=pk)
    return render(request, "trazabilidad/lotes/detalle.html", {
        "lote": lote,
        "puede_crear_caja": puede_crear_caja(lote),
        "puede_agregar_producto": bool(lote.ruta_proceso_id and origen_unico_lote(lote) is not None),
        "cajas": cajas_con_peso().filter(lote_produccion=lote).order_by("pk"),
        "consumos": lote.consumos.select_related("partida__detalle_recepcion__especie").order_by("pk"),
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def crear_lote(request, pk):
    partida = presentar_partida(get_object_or_404(partidas_con_disponibilidad(), pk=pk))
    form = CrearLoteForm(request.POST if request.method == "POST" else None,
                         initial={"fecha_elaboracion": timezone.localdate(), "cantidad_kg": partida.disponible_lote_kg})
    if request.method == "POST" and form.is_valid():
        try:
            lote = crear_lote_produccion(partida=partida, usuario=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Lote de producción creado y producto asignado correctamente.")
            return redirect("producto_terminado:detalle_lote", pk=lote.pk)
    return render(request, "trazabilidad/lotes/crear.html", {
        "partida": partida, "form": form, "compatible": puede_asignar_lote(partida),
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def agregar_producto_lote(request, pk):
    lote = get_object_or_404(lotes_con_totales(), pk=pk)
    form = AgregarConsumoLoteForm(request.POST if request.method == "POST" else None, lote=lote)
    if request.method == "POST" and form.is_valid():
        try:
            agregar_consumo_lote(lote=lote, usuario=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, "Producto agregado al lote correctamente.")
            return redirect("producto_terminado:detalle_lote", pk=lote.pk)
    return render(request, "trazabilidad/lotes/agregar.html", {
        "lote": lote, "form": form, "hay_seguimientos": form.fields["partida"].queryset.exists(),
    })


@personal_operativo_requerido
@require_http_methods(["GET", "POST"])
def crear_caja(request, pk):
    lote = get_object_or_404(lotes_con_empaque(), pk=pk)
    composiciones = ComposicionCajaFormSet(request.POST if request.method == "POST" else None, prefix="composiciones")
    form = CajaForm(request.POST if request.method == "POST" else None)
    errores = []
    if request.method == "POST" and all([composiciones.is_valid(), form.is_valid()]):
        datos = [form.cleaned_data for form in composiciones
                 if form.cleaned_data and not form.cleaned_data.get("DELETE", False)]
        try:
            caja = crear_caja_service(lote=lote, composiciones=datos, peso_neto_kg=form.cleaned_data["peso_neto_kg"], usuario=request.user)
        except ValidationError as error:
            errores = error.messages
            lote = lotes_con_empaque().get(pk=lote.pk)
        else:
            messages.success(request, "Caja registrada correctamente.")
            return redirect("producto_terminado:detalle_caja", pk=caja.pk)
    return render(request, "trazabilidad/cajas/crear.html", {
        "lote": lote, "composiciones": composiciones, "errores": errores,
        "form": form, "peso_maximo_caja_kg": PESO_MAXIMO_CAJA_KG,
        "compatible": puede_crear_caja(lote),
        "pesos_presentaciones": {str(p.pk): format(p.peso_nominal_kg, ".2f")
                                  for p in PresentacionBolsa.objects.filter(activo=True)},
    })


@personal_operativo_requerido
def lista_cajas(request):
    cajas = cajas_con_peso()
    filtro = CajaFiltroForm(request.GET)
    if filtro.is_valid():
        q = filtro.cleaned_data["q"]
        if q:
            busqueda = Q(lote_produccion__codigo_lote__icontains=q) | Q(lote_produccion__especie__nombre__icontains=q)
            if q.isdecimal() and len(q) <= 19 and int(q) <= 9223372036854775807:
                busqueda |= Q(pk=int(q))
            cajas = cajas.filter(busqueda)
    else:
        cajas = cajas.none()
    pagina = Paginator(cajas.order_by("-fecha_armado", "-pk"), 10).get_page(request.GET.get("page"))

    def enlace(numero):
        parametros = request.GET.copy()
        parametros["page"] = numero
        return "?" + parametros.urlencode()

    return render(request, "trazabilidad/cajas/lista.html", {
        "filtro": filtro, "page_obj": pagina,
        "pagina_anterior": enlace(pagina.previous_page_number()) if pagina.has_previous() else None,
        "pagina_siguiente": enlace(pagina.next_page_number()) if pagina.has_next() else None,
    })


@personal_operativo_requerido
def detalle_caja(request, pk):
    caja = get_object_or_404(cajas_con_peso(), pk=pk)
    return render(request, "trazabilidad/cajas/detalle.html", {
        "caja": caja,
        "composiciones": caja.composiciones.select_related("presentacion").order_by("pk"),
    })


@never_cache
@personal_operativo_requerido
@require_http_methods(["GET"])
def consulta_caja_qr(request, identificador):
    caja = get_object_or_404(cajas_con_trazabilidad(), identificador_qr=identificador)
    return render(request, "trazabilidad/cajas/consulta_qr.html", {
        "caja": caja, "composiciones": caja.composiciones_qr,
        "aportes": caja.lote_produccion.aportes_qr,
        "puede_ver_recepcion": tiene_rol(request.user, ROL_JEFE, ROL_ENCARGADA),
    })


@never_cache
@personal_operativo_requerido
@require_http_methods(["GET"])
def caja_qr_png(request, pk):
    caja = get_object_or_404(Caja, pk=pk)
    return HttpResponse(generar_png_qr_caja(request, caja), content_type="image/png")
