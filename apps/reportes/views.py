from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from urllib.parse import urlencode
from django.utils.translation import override
from django.views.decorators.http import require_http_methods

from apps.trazabilidad.models import UnidadFrio
from apps.usuarios.permisos import jefe_requerido
from .forms import ReporteSernapescaForm, UmbralFrioForm
from .selectors import dashboard_operacional, resumen_reporte_sernapesca
from .services import configurar_umbral, generar_reporte_apoyo_sernapesca
from .excel_sernapesca import NOTA_ALCANCE, NOTA_REFERENCIAS


@jefe_requerido
@require_http_methods(["GET"])
def dashboard(request):
    return render(request, "reportes/dashboard.html", dashboard_operacional(request.GET.get("periodo", "semana")))


@jefe_requerido
@require_http_methods(["GET", "POST"])
def umbrales(request):
    forms = []
    unidad_editada = None
    if request.method == "POST":
        pk = request.POST.get("unidad", "")
        unidad_editada = get_object_or_404(UnidadFrio, pk=int(pk) if pk.isdecimal() else 0)
    with override("es"):
        for unidad in UnidadFrio.objects.all():
            enviada = unidad_editada is not None and unidad.pk == unidad_editada.pk
            form = UmbralFrioForm(request.POST if enviada else None, instance=unidad, prefix=f"u{unidad.pk}")
            if enviada and form.is_valid():
                try:
                    configurar_umbral(usuario=request.user, unidad=unidad, horas=form.cleaned_data["umbral_alerta_horas"])
                except ValidationError as error:
                    form.add_error(None, " ".join(error.messages))
                else:
                    messages.success(request, "Umbral actualizado correctamente.")
                    return redirect("reportes:umbrales")
            forms.append(form)
        return render(request, "reportes/umbrales.html", {"formularios": forms})


@never_cache
@jefe_requerido
@require_http_methods(["GET"])
def sernapesca(request, descargar=False):
    with override("es"):
        form = ReporteSernapescaForm(request.GET)
        contexto = {"form": form, "nota_alcance": NOTA_ALCANCE, "nota_referencias": NOTA_REFERENCIAS}
        valido = form.is_valid()
        if valido:
            inicio, fin = form.cleaned_data["fecha_inicio"], form.cleaned_data["fecha_fin"]
            if descargar:
                contenido, nombre = generar_reporte_apoyo_sernapesca(
                    usuario=request.user, fecha_inicio=inicio, fecha_fin=fin,
                )
                response = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                response["Content-Disposition"] = f'attachment; filename="{nombre}"'
                response["X-Content-Type-Options"] = "nosniff"
                return response
            parametros = {"fecha_fin": fin.isoformat()}
            if inicio:
                parametros["fecha_inicio"] = inicio.isoformat()
            resumen = resumen_reporte_sernapesca(inicio, fin)
            contexto.update(resumen=resumen, sin_datos=not any(resumen.values()), inicio=inicio, fin=fin,
                            descarga=reverse("reportes:descargar_sernapesca") + "?" + urlencode(parametros))
        return render(request, "reportes/sernapesca.html", contexto, status=200 if valido else 400)
