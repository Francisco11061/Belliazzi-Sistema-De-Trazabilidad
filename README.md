# Belliazi-Sistema-De-Trazabilidad
Sistema web de trazabilidad e inventario para Pesquera Belliazzi

## Estilos del inicio de sesión

Los estilos utilizan Tailwind CSS y el plugin de Flowbite. Para regenerarlos,
instala Node.js con npm y ejecuta desde la raíz del proyecto:

```sh
npm ci
npm run build:css
```

Edita `static/src/input.css` y las plantillas de Django. La compilación genera
`apps/usuarios/static/usuarios/css/output.css`, que debe incluirse en el
repositorio para servir el login sin compilar CSS en tiempo de ejecución.
Vuelve a compilar después de cambiar estilos o clases de las plantillas.

## Gestión interna de usuarios

`/usuarios/` permite al JEFE y al superusuario técnico listar, crear, consultar,
cambiar roles, restablecer contraseñas y desactivar/reactivar cuentas. Se reutilizan
`Usuario`, `Rol` y los permisos existentes. Solo se asignan roles funcionales activos.
No existe registro público ni eliminación; el nombre de usuario permanece estable.

Las altas y resets requieren una contraseña temporal validada por Django. El usuario
debe cambiarla en `/mi-contrasena/` antes de acceder a otras vistas. El reset exige
una contraseña diferente, invalida las sesiones anteriores y no reactiva cuentas
inactivas. El superusuario técnico conserva su acceso sin rol y está exento del
cambio obligatorio. Nunca se guardan contraseñas o hashes en el historial.

La migración `usuarios.0003_eventousuario` agrega exclusivamente el historial
`EventoUsuario`: actor, fecha, acción y nombres/códigos de roles para los cambios.
No altera usuarios existentes ni reconstruye eventos históricos. Las operaciones
y su evento se guardan en una misma transacción. El historial no admite edición
ni eliminación desde el modelo, los servicios o el Admin.

Los servicios releen los permisos y bloquean los roles en orden antes de modificar
usuarios. Esto serializa la gestión y protege al último JEFE funcional activo,
incluso ante cambios simultáneos. Un superusuario no cuenta como JEFE de negocio.
No se permite desactivar la propia cuenta; un autocambio de rol requiere otro
JEFE activo y redirige al inicio sin cerrar la sesión.

Las cuentas `is_staff` o `is_superuser` son de mantenimiento técnico y no se
modifican desde esta interfaz. Django Admin queda reservado al superusuario para
usuarios y roles; no permite eliminarlos ni editar el historial. Como vía de
mantenimiento, conserva la edición técnica de permisos, roles y estados mediante
el Admin estándar: esos cambios usan su propio `LogEntry` y **no** aplican las
reglas operativas del último JEFE ni generan `EventoUsuario`. Para gestión normal,
usar `/usuarios/`. El técnico puede crear allí el primer JEFE sin cambiar su cuenta.

Validación del módulo: `uv run python manage.py test apps.usuarios`. La suite
incluye permisos, contraseña temporal, sesiones, auditoría, conservación de
recepciones históricas y concurrencia sobre MySQL.

## Dashboard operacional y alertas de frío

`/dashboard/` está disponible para JEFE y superusuario, con acceso desde Inicio
y el sidebar. `apps.reportes` concentra las lecturas en `selectors.py`; no guarda
métricas ni alertas. Reutiliza `cajas_con_inventario`, `resumen_inventario`,
`totales_stock` y `despachos_con_totales`. El antiguo `resumen_stock` de reportes
también usa ahora el estado oficial y el peso neto real, conservando sus claves
de respuesta para compatibilidad.

- Stock y cajas almacenadas: exclusivamente estado ALMACENADA de Inventario.
- Pendientes: estado PENDIENTE del mismo selector, sin despachos ni bajas.
- Despachos de hoy: fecha/hora de despacho dentro del día actual de Chile;
  los kg se suman desde `DetalleDespacho.caja.peso_neto_kg`.
- Stock por especie: agregación de Inventario, sin pesos nominales ni teóricos.
- Semana: lunes a domingo de la semana actual. Mes y año: calendario actual.
  `?periodo=semana|mes|ano`; cualquier otro valor usa semana. El período afecta
  la serie de despachos, las mermas y las bajas, no el stock ni las alertas actuales.
- Series: agrupaciones SQL por intervalos aware, con límites calculados en
  `America/Santiago`. Respetan cambios de horario sin requerir tablas de zonas
  horarias cargadas en MySQL. Los intervalos son inicio incluido/fin excluido.
- Mermas: suma por tipo de `MermaProceso`, según fecha/hora del evento.
  Bajas: cantidad y peso real conocido de `BajaCaja`, por fecha/hora del evento,
  siempre separadas de las mermas. NULL no aporta kg; se informa como desconocido.
- Actividad: hasta ocho registros combinados de recepción, lote, despacho y baja,
  ordenados por `creado_en`, con enlace al recurso. No reconstruye fechas faltantes.

La migración `trazabilidad.0013` agrega `UnidadFrio.umbral_alerta_horas`, Decimal
nullable de dos decimales, con validación y constraint positivo. No asigna valores
a las unidades históricas. `/dashboard/umbrales/` permite establecer, modificar o
quitar cada umbral; campo vacío significa NULL. Solo JEFE y superusuario pueden
guardar. El Admin técnico también puede editarlo; para otros usuarios es readonly.

Las alertas evalúan estancias abiertas de seguimiento y caja: duración real
transcurrida > umbral. Exactamente en el umbral no alertan. Se calcula en UTC con
timestamps aware, y se muestra en horas/minutos; excesos inferiores a un minuto
se indican expresamente. Unidades inactivas con estancias aún abiertas siguen
evaluándose. Cerradas o sin umbral no alertan. Se filtran en SQL y se muestran las
diez permanencias con ingreso más antiguo, junto al total. Una estancia abierta
histórica se evalúa como tal, sin modificarla ni inferir una salida. Los avisos
de cajas pendientes y peso desconocido derivan directamente de Inventario.

Chart.js 4.5.1 está fijado en npm y se sirve localmente; no hay CDN. Para regenerar
los recursos después de `npm ci`:

```sh
npm run build:charts
npm run build:css
```

`build:charts` copia el bundle UMD y su licencia a
`apps/reportes/static/reportes/vendor/`, incluidos en el repositorio para servir
la aplicación sin Node en producción. Los gráficos reciben `json_script` seguro;
su lógica vive en `static/reportes/dashboard.js`. Los datos textuales permanecen
accesibles si Chart.js o JavaScript no cargan; el filtro funciona en el servidor.
No hay polling, notificaciones externas ni modificaciones de stock.

Pruebas específicas: `uv run python manage.py test apps.reportes`.

## Reporte de apoyo a Sernapesca (HU12)

Disponible en **Reportes** (`/reportes/`) para JEFE y superusuario técnico.
Permite revisar cantidades de filas por período y descargar un único XLSX con
openpyxl, generado en memoria. No envía información a Sernapesca ni sustituye
sus declaraciones oficiales. El endpoint de descarga también exige permisos.

Las fechas son opcionales: desde el primer registro y hasta hoy en
`America/Santiago`. Cada hoja filtra su evento correspondiente; el día final
se incluye completo. Fechas inválidas o invertidas devuelven errores de formulario.

| Hoja | Unidad y origen de los datos | Columnas |
| --- | --- | --- |
| Resumen | Metadatos y criterios de lectura | Autor, generación, rango, zona horaria, cantidades de filas y explicación de pesos e históricos. |
| Abastecimiento | Una fila por DetalleRecepcion, con Recepcion, OrigenSernapesca y Especie. Fecha de recepción. | ID recepción, fecha/hora Chile, folio, tipo origen, agente, proveedor, especie, peso origen, peso recibido, diferencia recibido menos origen, usuario, observaciones. |
| Producción | Una fila por LoteProduccion. Fecha de elaboración. | Fecha elaboración, código original, folio, especie, ruta, IDs seguimientos, postproceso conocido de referencia, kg asignados, merma/descarte/pérdida de referencia, cantidad cajas, neto real conocido, cajas sin peso, vencimiento, usuario, observaciones, notas de información incompleta. |
| Destino | Una fila por DetalleDespacho/caja. Fecha del despacho. | ID despacho, fecha/hora Chile, tipo destino, destinatario, RUT, país, tipo/número/fecha documento, ID caja, código lote, especie, neto real, folio, usuario, observaciones. |

La asignación suma `ConsumoLote.cantidad_kg_utilizada`; el empacado suma solo
`Caja.peso_neto_kg`. Las cajas NULL se cuentan aparte sin utilizar peso nominal.
Postproceso y mermas son referencias de los seguimientos completos: **no son
cantidades atribuibles al lote ni sumables entre lotes**. No se prorratean.
Si un seguimiento carece de pesaje postproceso o tiene varios, se excluye su peso
y se explica en las notas. Sin ningún peso postproceso conocido queda vacío.
Los aportes y cajas del lote son los documentados al generar el reporte, incluso
si fueron registrados después del período de elaboración seleccionado.

El folio siempre procede de las relaciones de ConsumoLote → PartidaProceso →
DetalleRecepcion → OrigenSernapesca; nunca se deduce del código. Los orígenes
históricos múltiples muestran identificadores y folios. Códigos antiguos, ceros
iniciales y campos opcionales se conservan; valores ausentes quedan vacíos.
Los textos se guardan como texto literal, incluso si comienzan con `=`.
Los caracteres de control incompatibles con XLSX se representan como `\uXXXX`.

`forms.py` valida fechas; `selectors.py` reúne datos usando joins, prefeteo y
las agregaciones existentes de lotes; `services.py` comprueba autorización y
coordina la descarga; `excel_sernapesca.py` concentra columnas y formato. La
vista solo valida y responde. Se mantienen las URLs existentes del dashboard.
No requiere modelos ni migraciones y no modifica registros operativos.

Instalación de dependencias: `uv sync`. Pruebas del bloque:
`uv run python manage.py test apps.reportes.test_sernapesca`.
