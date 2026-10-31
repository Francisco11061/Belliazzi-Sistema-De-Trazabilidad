# Historial del producto y acceso por QR

## Flujo y permisos

La URL de `consulta_caja_qr` conserva el mismo UUID. Sin sesión, Django redirige al login con `next`; después vuelve a la caja escaneada. No hay una segunda URL de QR, redirecciones intermedias ni una ficha pública.

Jefe, encargada y superusuario reciben la ficha integral existente mediante el mismo selector y el mismo renderizador que usa la búsqueda. El selector conserva su comprobación de permisos. Operarios mantienen la consulta operativa y sus datos previamente autorizados; no pueden abrir la ficha integral mediante URL directa. La consulta QR es de lectura: los enlaces para ingresar, mover o dar de baja se conservan en las pantallas operativas, no en esta consulta.

La ficha de caja identifica especie, código, lote, peso, contenido, estado, ubicación y fechas. Sus movimientos de almacenamiento, despachos, bajas y armado corresponden exclusivamente a esa caja. El origen y procesamiento permanecen asociados al lote; otras cajas pueden consultarse mediante un enlace explícito al historial del lote. La consulta de lote, origen, recepción, seguimiento o despacho conserva las ramificaciones existentes.

## Pesos y procedencia

No se cambiaron servicios, validaciones ni fórmulas de empaque. «Diferencia entre peso asignado y empacado» presenta el cálculo que antes se llamaba «Disponible para empacar». No afirma disponibilidad física ni registra pérdidas.

El control de pesos mantiene el alcance del lote y su origen incluso cuando se entra desde una caja; la interfaz lo aclara. El peso individual se muestra aparte en el resumen. Los valores incompletos permanecen sin calcular y no se sustituyen por cero.

Cuando hay más de un seguimiento aportante, aparece «Esta caja pertenece a un lote elaborado con producto de varios seguimientos», con ayuda desplegable. No se atribuyen kilos ni proporciones de cada aporte a una caja.

## Interfaz

«Ficha integral» pasa a «Historial del producto». Secciones: resumen, origen y recepción, historial de procesamiento, control de pesos, cajas, almacenamiento, despachos y bajas, historial de movimientos y cambios registrados. Se eliminaron los párrafos técnicos permanentes y se simplificaron también los textos de los seguimientos y pesos.

La cronología conserva fechas, responsables, identificadores internos y orden estable. Los títulos de operaciones muestran el nombre del trabajo y el seguimiento. Los cambios registrados usan nombres legibles de entidades y campos.

CSS propio, acotado a `.hp-history`, conserva cyan/slate/blanco. Tarjetas compactas, enlaces táctiles, navegación adaptable, foco visible y detalles desplegables sin JavaScript adicional. Se reutilizan los datos de caja y almacenamiento en la vista operativa. No se incorporaron dependencias.

## Archivos de esta ronda

- `apps/trazabilidad/views.py`, `views_consulta.py`: entrada QR según permisos y renderizador compartido.
- `apps/trazabilidad/consulta_integral.py`: selección de movimientos de la caja, contexto del resumen y nombres visibles de eventos/cambios.
- `apps/trazabilidad/templates/trazabilidad/consulta/ficha.html`, `_seguimiento.html`, `_peso.html`, nuevos `_resumen_caja.html`, `_lotes.html`, `_secciones.html`: ficha y textos.
- `apps/trazabilidad/templates/trazabilidad/cajas/consulta_qr.html`, `detalle.html`: consulta operativa y acceso al historial.
- `apps/trazabilidad/static/trazabilidad/historial.css`: presentación responsive.
- `apps/inventario/templates/inventario/_almacenamiento.html`: opción de solo lectura para el QR; los demás usos conservan sus acciones.
- `templates/components/_enlace_integral.html`: nombre del enlace compartido.
- `apps/trazabilidad/test_historial_producto.py`: pruebas nuevas del flujo y alcance.
- `apps/trazabilidad/test_consulta_integral.py`, `test_qr_cajas.py`, `apps/reportes/test_recorridos_qr.py`: expectativas actualizadas de textos y coste de la ficha integral frente a la consulta operativa.
- `apps/trazabilidad/test_procesamiento.py`: límite de consultas actualizado para incluir la única consulta del contador del sidebar de la ronda anterior.
- `apps/usuarios/static/usuarios/css/output.css`: Tailwind recompilado.

Los cambios de sidebar y la modificación previa del dashboard ya estaban en el workspace al comenzar esta ronda y se conservaron.

## Uso local y etiquetas existentes

Iniciar el servidor y abrir la URL UUID de una caja existente, con y sin sesión. Jefe/encargada deben obtener el historial integral; operario, la consulta operativa. No es necesario regenerar etiquetas ni aplicar migraciones.

`qr.py` sigue construyendo la URL absoluta desde el dominio de la petición. Para imprimir etiquetas definitivas se debe acceder por el dominio HTTPS real de Belliazzi, con la configuración correcta del servidor/proxy. Esta entrega no configura un dominio ni despliega. Una etiqueta impresa con `localhost` seguirá apuntando a ese host; el UUID se conserva, pero esa dirección no conecta desde otro dispositivo al servidor de la planta.

## Límites conservados

No existe una asignación física de aportes a cada caja. El historial muestra las relaciones registradas, sin inventarla. La carga de la ficha mantiene la estrategia existente de recorrer la familia y cargar relaciones antes de paginar: no agrega consultas por cada caja, pero historiales extraordinariamente grandes siguen requiriendo más memoria. No se creó una segunda implementación ni se alteraron registros históricos.

No se cambiaron UUID, modelos, tablas, autenticación ni permisos. No se hicieron commit, push, merge o despliegue.

## Validación ejecutada

- 196 tests relacionados: todos correctos. Incluyen las 10 pruebas nuevas del historial, consulta integral, QR (incluida su prueba de migración en base descartable), recorridos QR, inventario, matriz de permisos, procesamiento y seguimiento operativo.
- Cuatro escenarios adicionales renderizados desde tests: correctos.
- Edge mediante Playwright en 320, 375, 390, 768, 1024 y 1440 px: 30 combinaciones de lote, caja almacenada/despachada/de baja y consulta operativa. Sin overflow de página, enlaces de sección rotos, controles menores a 44 px ni errores JavaScript; se verificaron también los desplegables abiertos, teclado y foco.
- Se inspeccionaron capturas de móvil y escritorio. Las páginas del navegador proceden de templates reales con datos de test, no de una sesión contra la base real.
- Comprobación adicional de la vista QR con una caja de la base local, solo lectura: HTTP 200.
- `manage.py check` y `git diff --check`: correctos. `makemigrations --check --dry-run`: sin cambios. Tailwind recompilado con los argumentos de `build:css`.
- No se ejecutó la suite completa del proyecto en esta ronda; los 196 tests corresponden al alcance relacionado solicitado.

Artefactos de diagnóstico locales: `.git/historial-final-tests.log` y `.git/historial-preview/`.
