document.querySelectorAll('[data-secuencia]').forEach(contenedor => {
    const filas = contenedor.querySelector('[data-filas]');
    const total = contenedor.querySelector('input[name$="-TOTAL_FORMS"]');
    const activas = () => [...filas.children].filter(f => !f.querySelector('input[name$="-DELETE"]').checked);
    const actualizar = () => {
        const nombres = [];
        activas().forEach(f => {
            const select = f.querySelector('select');
            const orden = f.querySelector('input[name$="-ORDER"]');
            if (select.value) {
                nombres.push(select.selectedOptions[0].textContent);
                orden.value = nombres.length;
            } else orden.value = '';
        });
        contenedor.querySelector('[data-resumen]').textContent = nombres.join(' → ');
    };
    contenedor.querySelector('[data-agregar]').addEventListener('click', () => {
        if (Number(total.value) >= 100) return;
        const html = contenedor.querySelector('[data-vacia]').innerHTML.replaceAll('__prefix__', total.value);
        filas.insertAdjacentHTML('beforeend', html);
        total.value = Number(total.value) + 1;
        filas.lastElementChild.querySelector('select').focus();
    });
    filas.addEventListener('change', e => {
        if (e.target.matches('select, input[name$="-DELETE"]')) actualizar();
    });
    filas.addEventListener('click', e => {
        const fila = e.target.closest('[data-fila]');
        const lista = activas(), indice = lista.indexOf(fila);
        if (e.target.closest('[data-subir]') && indice > 0) filas.insertBefore(fila, lista[indice - 1]);
        else if (e.target.closest('[data-bajar]') && indice >= 0 && indice < lista.length - 1) lista[indice + 1].after(fila);
        else return;
        actualizar();
        e.target.focus();
    });
    // Conservar órdenes enviados al volver de un error; no reescribir al cargar.
});
