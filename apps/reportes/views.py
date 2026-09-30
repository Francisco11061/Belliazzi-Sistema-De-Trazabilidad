from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import override
from django.views.decorators.http import require_http_methods

from apps.trazabilidad.models import UnidadFrio
from apps.usuarios.permisos import jefe_requerido
from .forms import UmbralFrioForm
from .selectors import dashboard_operacional
from .services import configurar_umbral


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
