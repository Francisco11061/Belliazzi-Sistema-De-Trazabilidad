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
