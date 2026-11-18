(() => {
    const bloque = document.querySelector('[data-origen-embarcacion]');
    if (!bloque) return;

    const tipo = bloque.querySelector('[name="tipo_origen"]');
    const datos = bloque.querySelector('[data-datos-embarcacion]');
    const nombre = datos.querySelector('[name="nombre_embarcacion"]');
    const matricula = datos.querySelector('[name="matricula_embarcacion"]');
    const rpa = datos.querySelector('[name="rpa_embarcacion"]');
    const lista = datos.querySelector('#embarcaciones-guardadas');
    const guardadas = JSON.parse(datos.querySelector('#datos-embarcaciones-guardadas').textContent);
    const normalizar = valor => valor.trim().toLocaleLowerCase('es-CL');
    const porNombre = new Map();
    for (const barco of guardadas) {
        const clave = normalizar(barco.nombre_embarcacion);
        if (!porNombre.has(clave)) porNombre.set(clave, []);
        porNombre.get(clave).push(barco);
    }
    const opciones = new Map();
    let autocompletada = null;

    for (const barcos of porNombre.values()) {
        for (const barco of barcos) {
            const titulo = barcos.length === 1 ? barco.nombre_embarcacion
                : [barco.nombre_embarcacion, barco.matricula_embarcacion, barco.rpa_embarcacion]
                    .filter(Boolean).join(' — ');
            opciones.set(normalizar(titulo), barco);
            lista.append(new Option(titulo, titulo));
        }
    }

    function mostrarEmbarcacion() {
        const visible = tipo.value === bloque.dataset.origenEmbarcacion;
        datos.hidden = !visible;
        datos.disabled = !visible;
    }

    nombre.addEventListener('input', () => {
        const barco = opciones.get(normalizar(nombre.value));
        if (barco) {
            nombre.value = barco.nombre_embarcacion;
            matricula.value = barco.matricula_embarcacion;
            rpa.value = barco.rpa_embarcacion;
        } else if (autocompletada) {
            if (matricula.value === autocompletada.matricula_embarcacion) matricula.value = '';
            if (rpa.value === autocompletada.rpa_embarcacion) rpa.value = '';
        }
        autocompletada = barco || null;
    });
    tipo.addEventListener('change', mostrarEmbarcacion);
    mostrarEmbarcacion();
})();
