# Centro de control administrativo

## Resultado

El dashboard conserva Django Templates, los selectores existentes y Chart.js local. Presenta, en este orden: filtros y actualización, cuatro KPI actuales, seis accesos, asuntos pendientes, estado de producción, cuatro gráficos, vencimientos y antigüedad, análisis productivo y actividad reciente.

Los KPI muestran stock documentado y cajas, seguimientos en proceso, seguimientos listos para empaque con saldo real sin asignar y motivos de atención. La interfaz aclara que un registro puede generar más de un motivo. Las mermas y bajas normales no se presentan automáticamente como incidentes.

Los accesos llevan a recepción, seguimiento, nueva ruta, gestión de rutas, consulta integral y despacho. Utilizan funcionalidades existentes, incluidas las implementadas en la ronda anterior.

## Archivos de esta entrega

Nuevos:

- `apps/reportes/control_operativo.py`: consultas operativas de lectura y enlaces filtrados.
- `apps/reportes/static/reportes/dashboard.css`: estilos específicos y responsive.
- `apps/reportes/templates/reportes/dashboard/_icono.html`, `_grafico.html`, `_stock.html`, `_analisis.html`: componentes del dashboard.
- `apps/reportes/test_centro_control.py`: 16 pruebas del centro de control.
- `docs/dashboard_centro_control.md`: este informe.

Modificados:

- `apps/reportes/selectors.py`, `templates/reportes/dashboard.html`, `static/reportes/dashboard.js`: datos y presentación.
- `apps/inventario/forms.py`, `selectors.py`, `views.py`, `templates/inventario/despachos/lista.html`: filtros de destino por especie, período y falta de peso; totales del despacho según especie seleccionada.
- `apps/trazabilidad/views.py`: listado de seguimientos actuales filtrado por situación, sin excluir categorías operativas relevantes.
- `apps/trazabilidad/views_rutas.py`: filtro por especie en solicitudes de rutas.
- `apps/usuarios/views.py`, `templates/base.html`: destino inicial del jefe y menú.
- `apps/usuarios/static/usuarios/css/output.css`: compilación Tailwind.
- Tests de reportes (`test_alineacion_dashboard`, `test_dashboard`, `test_matriz_permisos`, `test_recorridos_qr`, `test_sernapesca`), usuarios (`test_gestion`, `test_inicio_operario`, `tests`), inventario (`test_despachos`) y trazabilidad (`tests`): expectativas de navegación y definiciones de indicadores ajustadas.

El repositorio ya tenía cambios de rutas y consulta integral sin commit. Esos archivos, modelos y la migración `0014_solicitudes_rutas` pertenecen a la ronda anterior; no son nuevos cambios de esquema del dashboard. Se retiró de la vista de reportes una consulta de solicitudes duplicada: ahora la proporciona el selector con el filtro de especie.

## Consultas e indicadores

- Se reutilizan `dashboard_operacional`, `cajas_con_inventario`, el resumen de inventario y `partidas_con_disponibilidad`, que incorpora la situación operacional existente.
- Producción actual: agregación por situación de seguimientos activos sin hijas. No se cuentan padres divididos como producto adicional.
- Empaque: suma del saldo postproceso disponible para lotes; pesos múltiples o saldos negativos se identifican como incompletos.
- Atención: permanencia en frío, pendientes de ingreso, ubicaciones inconsistentes, stock sin peso, seguimientos por revisar y solicitudes pendientes.
- Ranking: agregación de detalles de despacho por especie y período, limitada a cinco. No se usan bajas como despachos.
- Cámaras: se reutiliza el agrupamiento de stock almacenado; enlaces conservan cámara y especie.
- Vencimientos: conteos distintos de lotes con cajas realmente almacenadas. Lista limitada por SQL a ocho lotes, priorizando vencidos y próximos.
- Antigüedad: seis cajas con ingreso a su estancia abierta más antiguo. La fecha de armado se muestra separadamente.
- Rendimiento: peso postproceso dividido por peso inicial de los mismos seguimientos. Se excluyen padres divididos y registros con múltiples pesajes ambiguos; se informa esta exclusión.
- Cajas producidas: se usa su fecha real de armado, no la fecha de elaboración del lote.
- Alertas de frío: duración y umbrales existentes; se excluyen cajas terminales o inconsistentes y seguimientos históricos. No son mediciones de temperatura.

Las fechas afectan los resultados del período, no el inventario ni la producción actuales. El filtro de especie se conserva en métricas compatibles y sus enlaces. Se mantienen límites horarios conscientes de la zona de Chile.

## Gráficos

1. Dona de stock por especie: seis principales y «Otras», porcentajes del peso conocido y tabla con desglose completo.
2. Barras horizontales: cinco especies con más kg despachados en el período.
3. Barras horizontales: kg almacenados por cámara, con cajas como información complementaria.
4. Barras temporales: evolución de kg despachados.

Los cuatro tienen tablas accesibles, unidades y tooltips con formato chileno. Los pesos faltantes se distinguen de cero. Sin JavaScript, las tablas permanecen abiertas; si falla un gráfico se conserva la alternativa textual. No se incorporaron dependencias frontend nuevas.

## Navegación y responsive

El jefe y el superusuario reciben el dashboard como destino predeterminado. Un `next` válido mantiene prioridad y se conserva el cambio obligatorio de contraseña. Operarios y encargada conservan Inicio y no reciben permisos administrativos. El menú del jefe elimina el acceso Inicio redundante; su URL antigua redirige al dashboard.

Cuatro KPI y dos gráficos por fila cuando hay espacio; reducción progresiva a dos y una columna. Los seis accesos se reorganizan en seis, tres o dos columnas. Filtros plegables en móvil, controles de al menos 44 px, tablas con desplazamiento local y foco visible. No se cambió el drawer compartido.

## Límites y datos faltantes

- Los kg representan únicamente pesos documentados. Los totales parciales lo indican.
- La antigüedad representa la estancia abierta actual, no una permanencia acumulada entre cámaras.
- La ventana de vencimientos usa `DASHBOARD_VENCIMIENTOS_DIAS` si está configurado; por defecto 30 días, acotada entre 1 y 365. No se inventan vencimientos ni se descarta producto automáticamente.
- Se muestran ocho lotes, seis cajas antiguas y ocho actividades recientes; el usuario puede continuar a los listados.
- No hay capacidades físicas de cámaras, temperaturas, OEE ni notificaciones persistentes nuevas.
- El dashboard es de lectura: no escribe indicadores ni altera registros históricos.

## Validación ejecutada

- `uv run python manage.py check`: sin problemas.
- `uv run python manage.py test --noinput`: 666 tests, 664 correctos y dos fallos preexistentes del aviso de Sernapesca. Las 16 pruebas nuevas del centro de control pasan. La suite no está completamente verde.
- Los fallos son `apps.reportes.test_microcopy.MicrocopyAvisosTests.test_reportes_conserva_aviso_oficial_y_remite_al_excel` y `apps.reportes.test_sernapesca.ReporteSernapescaTests.test_pantalla_y_urls_existentes`: ambos esperan el aviso «Reporte de apoyo. No reemplaza las declaraciones oficiales ante Sernapesca.» ausente en esa pantalla, que no se modificó.
- Las pruebas incluyen permisos, `next`, cambio obligatorio de contraseña, inventario actual independiente de fechas, divisiones, filtros, pesos ambiguos, vencimientos y consultas sin crecimiento N+1. Se conservan las pruebas existentes de fechas y zona horaria.
- Tres escenarios adicionales renderizados desde tests: con datos de varias especies, stock sin pesos documentados y sin datos; los tres pasan.
- Edge mediante Playwright: 320, 375, 390, 768, 1024 y 1440 px para los tres escenarios (18 combinaciones). Sin overflow horizontal de página, errores JavaScript ni controles visibles del dashboard menores a 44 px. Los cuatro gráficos se construyen en el escenario poblado.
- Revisión de capturas móvil/escritorio; apertura de filtros con Enter y foco visible. La prueba detectó inicialmente ausencia de foco en el summary; se añadió una regla explícita y la repetición pasó.
- Sin JavaScript: filtros y tabla de datos accesibles. La revisión usa HTML real renderizado por Django con datos de test, no una sesión contra la base real.
- Tailwind recompilado con la CLI y los mismos argumentos de `build:css`.
- `git diff --check`: correcto (solo avisos informativos de conversión LF/CRLF).
- `uv run python manage.py makemigrations --check --dry-run`: no detecta cambios. No se generaron migraciones del dashboard ni se aplicaron migraciones a la base del proyecto.

Los registros y capturas locales están bajo `.git/dashboard-preview/`; el log de la suite está en `.git/dashboard-validacion-final.log`. Estos artefactos de diagnóstico no forman parte del código versionado.

## Prueba local

1. En un entorno de desarrollo con respaldo y configuración local, comprobar `uv run python manage.py showmigrations trazabilidad`.
2. La base del workspace todavía tiene pendiente `0014_solicitudes_rutas`, de la funcionalidad anterior. Para usar esa funcionalidad y su contador, aplicar las migraciones **en la base de desarrollo elegida** con `uv run python manage.py migrate`. Esta entrega no ejecutó ese comando sobre la base del proyecto.
3. Ejecutar `uv run python manage.py runserver`, iniciar sesión como jefe y abrir `/dashboard/`.
4. Probar especie y períodos, enlaces de KPI, tablas de gráficos, vencimientos y estados sin peso. Los indicadores actuales deben permanecer independientes del rango de fechas.

No se hicieron commit, push, merge ni despliegue. No se insertaron datos de prueba en la base del proyecto: los escenarios del navegador se renderizaron desde una base de tests descartable.
