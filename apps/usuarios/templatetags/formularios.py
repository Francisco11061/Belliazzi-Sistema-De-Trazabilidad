from django import template

register = template.Library()


@register.inclusion_tag('components/_form_errors.html', takes_context=True)
def resumen_errores(context):
    formularios = [(context.get('form'), ''), (context.get('origen'), 'Origen: ')]
    for nombre in ('especies', 'composiciones', 'secuencia'):
        formset = context.get(nombre)
        if getattr(formset, 'is_bound', False):
            nombres = {'especies':'Especie', 'composiciones':'Presentación', 'secuencia':'Operación'}
            formularios.extend((form, f'{nombres[nombre]} {numero}: ') for numero, form in enumerate(formset.forms, 1))
    enlaces, generales = [], bool(context.get('errores'))
    for form, prefijo in formularios:
        if not getattr(form, 'is_bound', False) or not form.errors:
            continue
        if getattr(form, 'cleaned_data', {}).get('DELETE'):
            continue
        generales = generales or bool(form.non_field_errors())
        for campo in form:
            if campo.errors and not campo.is_hidden:
                enlaces.append({'id':campo.id_for_label or campo.auto_id, 'nombre':prefijo + campo.label})
    for nombre in ('especies', 'composiciones', 'secuencia'):
        formset = context.get(nombre)
        if getattr(formset, 'is_bound', False):
            generales = generales or bool(formset.non_form_errors())
    return {'enlaces':enlaces, 'generales':generales}
