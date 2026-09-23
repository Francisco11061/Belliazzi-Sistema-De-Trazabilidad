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
