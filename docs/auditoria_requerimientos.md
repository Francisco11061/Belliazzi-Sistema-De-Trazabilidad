# Auditoría contra el informe 12-10

Fecha: 14-10-2026. Línea base: `5dfbc88` más los cambios previos del workspace. Se auditó el resumen de requisitos adjuntado; no se recibió el informe íntegro. Esta sección refleja el estado **antes** de implementar A–D. No se modificaron datos de producción. Las mediciones usan la base de pruebas.

| ID | Estado | Evidencia (archivo y función/vista/test) | Brecha |
|---|---|---|---|
| RF01 | Cumple | apps/trazabilidad/models.py: Recepcion, OrigenSernapesca, DetalleRecepcion; services.py: registrar_recepcion; tests.py: RegistroRecepcionServiceTests | Referencia de origen separada de codigo_lote; detalles por especie y dos pesos. |
| RF02 | Parcial | apps/trazabilidad/services.py: enviar_a_mantencion, retirar_de_mantencion; models.py: EstanciaPartida; test_partidas.py | Estancia opcional con ingreso/salida; el detalle muestra fechas, no duración individual calculada. Datos suficientes para calcularla. |
| RF03 | Parcial | apps/trazabilidad/models.py: EventoProceso, EtapaRutaProceso; services.py: iniciar_etapa_proceso, finalizar_etapa_proceso; test_procesamiento.py | Múltiples eventos configurados por ruta/especie con inicio/término. Falta duración individual visible. |
| RF04 | Parcial | apps/trazabilidad/services.py: enviar_a_tunel, retirar_de_tunel; models.py: EstanciaPartida; test_procesamiento.py | Túnel identificado e ingreso/salida conservados; falta permanencia individual visible. |
| RF05 | Cumple | apps/trazabilidad/services.py: registrar_peso_postproceso; selectors.py: resumen_pesajes_mermas; test_postproceso.py | Conserva cantidad inicial; peso positivo; superar inicial advierte sin bloquear. |
| RF06 | Cumple | apps/trazabilidad/models.py: Caja, ComposicionCaja, LoteProduccion; services.py: crear_caja; selectors.py: cajas_con_peso; test_cajas.py | Peso teórico calculado y peso neto medido separados; fechas pertenecen al lote. |
| RF07 | Parcial | apps/trazabilidad/qr.py: generar_png_qr_caja; models.py: Caja.identificador_qr; test_qr_cajas.py | UUID único y PNG; no hay diseño físico de etiqueta ni prueba de impresión/lectura en planta. |
| RF08 | Parcial | apps/trazabilidad/views.py: consulta_caja_qr; templates/trazabilidad/cajas/consulta_qr.html; test_qr_cajas.py | Ficha de solo lectura completa; UUID inexistente devuelve 404 estándar, sin mensaje específico de caja. |
| RF09 | Cumple | apps/inventario/views.py: ingresar; selectors.py: cajas_con_inventario; templates/inventario/_almacenamiento.html; test_almacenamiento.py | Stock exige estancia abierta válida y excluye despachos/bajas. |
| RF10 | Cumple | apps/inventario/services.py: registrar_despacho; selectors.py: cajas_para_despacho, despachos_con_totales; test_despachos.py | Selección de cajas disponibles, responsable, fecha, historial y totales. |
| RF11 | Cumple | apps/inventario/selectors.py: resumen_inventario, totales_stock | Agregación de cajas almacenadas por especie; sin peso se cuenta aparte. |
| RF12 | Parcial | apps/reportes/forms.py: ReporteSernapescaForm; views.py: sernapesca; services.py: generar_reporte_apoyo_sernapesca; test_sernapesca.py | Rango y XLSX existen; falta selección de operación. No envía declaraciones. |
| RF13 | Cumple | apps/trazabilidad/models.py: DetalleRecepcion → PartidaProceso → ConsumoLote → LoteProduccion → Caja; selectors.py: cajas_con_trazabilidad | Relaciones explícitas; soporta documentación incompleta histórica sin inventar origen. |
| RF14 | Cumple | apps/usuarios/views.py: LoginUsuarioView, LogoutUsuarioView, cambiar_contrasena, editar_usuario; middleware.py; test_gestion.py | Cambio obligatorio tras restablecimiento; next conservado por login. |
| RF15 | Cumple | apps/usuarios/views.py: crear_usuario, editar_usuario; services.py; test_gestion.py | Jefe crea/asigna; protección también en servicios. |
| RF16 | Cumple | apps/usuarios/views.py: estado_usuario; models.py: EventoUsuario; test_gestion.py | Desactivar/reactivar conserva historial. |
| RF17 | Cumple | apps/trazabilidad/services.py: registrar_merma_proceso; apps/inventario/models.py: BajaCaja; test_postproceso.py, test_bajas.py | Merma en kg y baja por caja con motivo, fecha y responsable; sin borrado del historial. |
| RF18 | Parcial | apps/trazabilidad/services.py: corregir_recepcion; models.py: Correccion; tests.py: CorreccionRecepcionTests | Corrección auditada de recepción existente; no hay corrección genérica de todos los módulos. Ampliarla excede autorización. |
| RF19 | Parcial | apps/reportes/selectors.py: dashboard_operacional; test_dashboard.py | Faltan rango libre, especie, recepción, postproceso, producción y tiempos. |
| RNF01 | Parcial | templates de Inicio, partidas/_siguiente_paso.html, procesamiento.html, cajas/seleccionar_lote.html | Véase conteo abajo; registrar caja desde listado requiere 4 acciones. |
| RNF02 | Parcial | apps/trazabilidad/test_qr_cajas.py: test_consulta_sin_n_mas_uno_al_aumentar_origenes | Medición: **10 consultas con 2 aportes y 10 con 3**, test OK. Sin N+1 en ese recorrido. No acredita latencia <3 s en red/equipo de planta. |
| RNF03 | Cumple | config/settings.py: MIDDLEWARE; apps/inventario/selectors.py; apps/reportes/selectors.py | Consultas en cada GET, sin caché de stock ni middleware de caché. No se midió SLA <5 s de infraestructura. |
| RNF06 | Parcial | Correccion, EventoUsuario; services de proceso/inventario; test_gestion.py, tests.py | Historial en flujos web; cierre completa término/salida del mismo evento. No existe inmutabilidad general a nivel de base para cualquier escritura administrativa. |
| RNF07 | Cumple | apps/usuarios/permisos.py y decoradores de las vistas; matriz siguiente | Sin discrepancias de distribución en las rutas funcionales actuales. Superusuario es bypass técnico explícito. |

## Permisos antes de los cambios

Notación: O=Operario, E=Encargada de registro, J=Jefe / Administrador. `200` para GET válido o `302` tras POST válido de acciones exclusivamente POST; denegados `403`, anónimo `302` a login. Un objeto inexistente puede dar 404, un método incorrecto 405 y un formulario inválido 200/400: no son errores de permisos. Las rutas exactas están en urls.py de cada app y urls_lotes.py.

| Vistas / nombres URL | Decorador | O | E | J | Anónimo | Discrepancia |
|---|---|---|---|---|---|---|
| trazabilidad: lista_partidas, detalle_partida, enviar_partida_mantencion, retirar_partida_mantencion, procesar_partida, gestionar_procesamiento, iniciar_etapa (POST), finalizar_etapa (POST), enviar_partida_tunel, retirar_partida_tunel (POST), registrar_peso_postproceso, registrar_merma_proceso | personal_operativo_requerido | 200/302 | 200/302 | 200/302 | 302 | Ninguna |
| producto_terminado: lista_cajas, detalle_caja, seleccionar_lote_caja, crear_caja, consulta_caja_qr, caja_qr_png | personal_operativo_requerido | 200 | 200 | 200 | 302 | Ninguna; crear_caja está bajo URL lotes pero registra cajas. |
| inventario: lista (vista inventario), ingresar, mover, crear_baja, lista_bajas | personal_operativo_requerido | 200 | 200 | 200 | 302 | Ninguna |
| trazabilidad: lista_recepciones, nueva_recepcion, detalle_recepcion | jefe_o_encargada_requerido | 403 | 200 | 200 | 302 | Ninguna |
| producto_terminado: lista_lotes, detalle_lote, crear_lote, agregar_producto_lote | jefe_o_encargada_requerido | 403 | 200 | 200 | 302 | Ninguna |
| inventario: lista_despachos, crear_despacho, detalle_despacho | jefe_o_encargada_requerido | 403 | 200 | 200 | 302 | Ninguna |
| trazabilidad: corregir_recepcion | jefe_requerido | 403 | 403 | 200 | 302 | Ninguna |
| reportes: dashboard, umbrales, sernapesca, descargar_sernapesca | jefe_requerido | 403 | 403 | 200 | 302 | Ninguna |
| usuarios: lista_usuarios, detalle_usuario, crear_usuario, editar_rol, reset_contrasena, desactivar_usuario (POST), reactivar_usuario (POST) | jefe_requerido | 403 | 403 | 200/302 | 302 | Ninguna |
| usuarios: inicio, cambiar_contrasena | login_required | 200 | 200 | 200 | 302 | Ninguna |
| usuarios: login, logout (POST) | LoginView / LogoutView | 302/200 según autenticación | igual | igual | 200/302 | Entrada/salida, no módulos restringidos. |

`/admin/` pertenece a Django Admin y usa is_staff/is_superuser, no el perfil de negocio. No se alterará esa arquitectura.

## RNF01: acciones desde Inicio antes de cambios

Se cuenta cada enlace/botón, incluida confirmación final, sin contar tecleo ni selección de campos. El sidebar persistente permite los mismos recorridos a O/E/J, aun cuando Inicio de E/J no tenga tarjetas de planta. En móvil abrir el drawer agrega **un clic** si se usa ese menú. Se distingue llegar al formulario de completar la operación.

| Etapa | Perfiles | Recorrido mínimo desde Inicio | Clics hasta formulario / registro |
|---|---|---|---|
| Mantención ingreso/retiro | O/E/J | Seguimiento → acción directa → confirmar | 2 / 3 |
| Inicio procesamiento | O/E/J | Seguimiento → Enviar a proceso → confirmar | 2 / 3 |
| Etapa de procesamiento | O/E/J | Seguimiento → Gestionar procesamiento → iniciar/finalizar | 2 / 3 |
| Ingreso a túnel | O/E/J | Seguimiento → Gestionar procesamiento → túnel → confirmar | 3 / 4 |
| Retiro de túnel | O/E/J | Seguimiento → detalle → retirar | 2 / 3 |
| Postproceso | O/E/J | Seguimiento → Registrar peso → confirmar | 2 / 3 |
| Armado de caja | O/E/J | Cajas → Registrar caja → seleccionar lote/continuar → registrar | 3 / 4 |
| Almacenamiento | O/E/J | Inventario → Ingresar → confirmar | 2 / 3 |
| Despacho | E/J | Despachos → Nuevo despacho → registrar | 2 / 3 |
| Merma | O/E/J | Seguimiento → detalle → Registrar merma → confirmar | 3 / 4 |
| Baja | O/E/J | Inventario → abrir ⋮ → Registrar baja → confirmar | 3 / 4 |

No se promete máximo 3 clics para toda secuencia de procesamiento: cada etapa tiene inicio y cierre, puede requerir selección de seguimiento y formularios. Los casos que necesitan nuevos datos/contexto o una vista nueva se reportan, no se fuerzan.

## Nomenclatura y extensiones

La migración de roles crea Encargada de registro y Jefe / Administrador; Rol.nombre_visible presenta Operario para OPERARIA. Sidebar/header y formularios usan esa propiedad. Superusuario sin rol se muestra como Administrador técnico: se alineará solo su presentación. No se cambiarán nombres almacenados ni códigos ni se reescribirá el historial de roles.

Extensiones respecto al resumen del informe: alertas de frío y umbrales; rutas configurables y división de seguimientos; pesos teórico/neto diferenciados; estados históricos ambiguos y alertas por documentación incompleta; controles de último Jefe y cambio obligatorio de clave; trazabilidad de múltiples aportes históricos. No son errores por no estar mencionadas.

## Límites detectados para fase 2

- alertas_frio devuelve solo excedidas, no estancias al 80 % ni dentro del umbral. Respetando «usa solo los datos que ya entrega», no se puede calcular esos dos estados; no se mostrará verde como garantía de permanencia segura. Se conservará el criterio estricto existente (> umbral).
- Producción usa fecha de elaboración del lote, igual que la hoja existente; no hay fecha de elaboración independiente en Caja.
- Rendimiento agregado será suma de pesos finales / suma de cantidades iniciales de los mismos seguimientos × 100; no promedio simple de porcentajes. Se usa el primer pesaje por fecha/pk, igual al detalle, sin duplicar el inicial por cada pesaje histórico.
- RNF02 no requiere optimización: el test existente ya fija 10 consultas y crecimiento constante. Se ampliará la cobertura por perfil.
- RF07 etiqueta física, RF08 mensaje específico, RF18 correcciones de otros módulos y RNF06 inmutabilidad global quedan fuera del alcance autorizado.

## Resultado de fase 2

### A — Dashboard

Implementados rango libre inclusive en Chile, especie, atajos con fechas y aria-current, validación con semana por defecto, actualización conservando GET y `never_cache`. Indicadores actuales por especie: stock, almacenadas, pendientes y almacenadas sin peso. Indicadores del período: recepciones/kg recibidos; kg iniciales y postproceso/rendimiento ponderado; cajas producidas/kg netos conocidos y desconocidos; cajas/kg despachados; merma/descarte/pérdida y bajas; promedio y número de estancias cerradas de mantención/túnel y eventos de procesamiento por tipo. Tiempos se atribuyen por fecha de término. Gráficos conservan alternativas textuales; tiempos usan tabla accesible.

Semáforo: pendientes y peso desconocido 0 verde / >0 ámbar; merma y bajas 0 verde / >0 ámbar; postproceso mayor al inicial ámbar; frío excedido rojo; falta global de umbrales ámbar. Franja actual prioriza críticos y luego suma pendientes + almacenadas sin peso + falta de umbrales. Mermas y postproceso no se mezclan con la franja actual. Indicadores sin umbral objetivo son neutros. Ámbar/rojo usan icono + texto. No se implementó 80 %/dentro del umbral: salida de alertas_frio insuficiente bajo la restricción recibida. No se agregó una constante 80 % sin uso ni se inventó una clasificación.

Agregaciones SQL sin consultas por registro. El selector completo pasa de un presupuesto de 20 a 23 consultas con los nuevos indicadores; test adicional comprueba número constante al aumentar registros. La actividad reciente filtrada por especie no muestra totales de despachos mixtos que pudieran confundirse con totales de esa especie.

### B — Sernapesca

Selector de operación, revisión filtrada, Excel con Resumen y solo la operación elegida (Todas conserva las tres), singular/plural, período/operación al descargar. CSV UTF-8 con BOM de una operación, mismas columnas; fechas en Chile y textos protegidos contra fórmulas de hoja de cálculo. Todas no ofrece CSV porque las operaciones tienen columnas diferentes. El servicio anterior queda intacto; la vista autorizada reutiliza selectors y generador para la selección. No envía declaraciones ni escribe registros.

### C — Permisos y nombres

No hubo discrepancias inequívocas de decoradores que corregir. Se añadió prueba con subTest de todas las URLs funcionales por O/E/J/anónimo, incluyendo métodos POST para acciones exclusivas; otro test verifica cobertura contra los URLconfs para detectar futuras rutas sin clasificar. Login/logout/next tienen prueba aparte. Los nombres se presentan por código desde un componente compartido, incluido el select del formulario, sin cambiar códigos, modelos o registros históricos. Superusuario se presenta como Jefe / Administrador, conservando su explicación técnica en el detalle.

### D — Recorridos y QR

Inicio ofrece Registrar caja directamente: Inicio → selección de lote → formulario → confirmar (3 clics, incluido confirmar). Encargada/Jefe tienen accesos a Seguimiento e Inventario en el contenido, evitando abrir el drawer. Merma tiene acceso en tarjetas/tabla: Inicio → Seguimiento → merma → confirmar (3). Baja tiene enlace visible en tarjeta: Inicio → Inventario → baja → confirmar (3). Despacho directo desde Inicio de E/J reduce a 2 clics. Mantención/proceso/postproceso/almacenamiento conservan 3. Retiro del túnel conserva 3. Ingreso al túnel sigue con 4: el listado no conoce si las etapas terminaron y el detalle tampoco recibe ese resumen; reducirlo sin mostrar acciones prematuras requiere contexto adicional de backend, fuera de D.

QR conserva 10 consultas con dos/tres aportes; nuevo test fija máximo 10 para los tres perfiles. Sin cambios de consultas porque no se encontró N+1. La matriz detectó además historial de estancia/evento cerrado con responsable NULL que rompía el template; ahora muestra «Responsable no documentado» sin alterar datos.

Revisión responsive por código: 375 px, KPIs actuales 2×2 y resto apilado; 768 px filtros/indicadores en columnas; 1280 px cuatro KPIs considerando offset del sidebar. Controles >=44 px y sin dependencia de hover; tablas con scroll local. No se efectuó prueba visual en navegador ni medición del tiempo de respuesta en planta.

### Pendientes que requieren una decisión fuera de esta autorización

1. Autorizar ampliar la salida de alertas_frio para distinguir 80 % y dentro del umbral.
2. Autorizar contexto de etapas completas en lista/detalle para acceso directo al túnel.
3. Definir etiqueta física, mensaje 404 específico y alcance de corrección auditada fuera de Recepciones.
4. Validar latencia QR/stock con equipo, red y volumen reales; 10 consultas no prueba el SLA.
5. Duración individual visible de cada estancia/evento (RF02–RF04): el detalle hoy muestra fechas. A incorpora promedios en Dashboard, pero no modifica el detalle fuera del alcance de D.

### Validación final

Validado el 15-10-2026: `uv run python manage.py test --noinput`: **614 tests OK**, 141,821 s. `uv run python manage.py check`: sin problemas. `git diff --check` y diff staged: correctos. Tailwind 4.3.3 recompilado mediante Node + CLI instalada con los argumentos de build:css, sin dependencias nuevas. Pruebas parciales: A 53, B 23, C 4 y D 9 aprobadas (algunas pruebas existentes se reutilizan entre conjuntos). Sin modelos/migraciones/dependencias nuevas ni modificaciones de datos de producción. Los cambios previos del workspace se conservan.

## Archivos modificados en A-D

- `apps/inventario/templates/inventario/_caja.html`
- `apps/reportes/excel_sernapesca.py`
- `apps/reportes/forms.py`
- `apps/reportes/selectors.py`
- `apps/reportes/templates/reportes/dashboard.html`
- `apps/reportes/templates/reportes/sernapesca.html`
- `apps/reportes/test_alineacion_dashboard.py`
- `apps/reportes/test_dashboard.py`
- `apps/reportes/test_matriz_permisos.py`
- `apps/reportes/test_operaciones_reporte.py`
- `apps/reportes/test_recorridos_qr.py`
- `apps/reportes/views.py`
- `apps/trazabilidad/templates/trazabilidad/partidas/_tabla.html`
- `apps/trazabilidad/templates/trazabilidad/partidas/_tarjetas.html`
- `apps/trazabilidad/templates/trazabilidad/partidas/detalle.html`
- `apps/usuarios/static/usuarios/css/output.css`
- `apps/usuarios/templates/usuarios/gestion/_campo_rol.html`
- `apps/usuarios/templates/usuarios/gestion/_rol.html`
- `apps/usuarios/templates/usuarios/gestion/formulario.html`
- `apps/usuarios/templates/usuarios/gestion/lista.html`
- `apps/usuarios/templates/usuarios/inicio.html`
- `apps/usuarios/test_badges.py`
- `apps/usuarios/test_gestion.py`
- `docs/auditoria_requerimientos.md`
- `static/src/input.css`
- `templates/base.html`
- `templates/components/_badge.html`
- `templates/components/_nombre_rol.html`
