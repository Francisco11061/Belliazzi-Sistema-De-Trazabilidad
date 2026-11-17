(() => {
    const form = document.querySelector('#despacho-form');
    if (!form) return;
    const tipo = form.querySelector('[name="tipo_destino"]');
    const actualizar = () => {
        const seleccionadas = [...form.querySelectorAll('[name="cajas"]:checked')];
        const centesimas = seleccionadas.reduce((total, caja) => total + Number(caja.dataset.peso), 0);
        document.querySelector('#total-cajas').textContent = seleccionadas.length;
        document.querySelector('#total-peso').textContent = (centesimas / 100).toLocaleString('es-CL', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' kg';
        form.querySelectorAll('[data-destino]').forEach(panel => { panel.hidden = panel.dataset.destino !== tipo.value; });
    };
    form.addEventListener('change', actualizar);
    actualizar();

    // Filtros locales: solo ocultan tarjetas; las cajas marcadas siguen en el envío.
    const panel = document.querySelector('#filtro-cajas');
    const tarjetas = [...document.querySelectorAll('[data-caja]')];
    if (!panel || !tarjetas.length) return;
    panel.style.display = '';
    const campo = nombre => panel.querySelector(`[data-f="${nombre}"]`);
    const llenar = (select, clave) => [...new Set(tarjetas.map(t => t.dataset[clave]))].sort()
        .forEach(valor => select.append(new Option(valor, valor)));
    llenar(campo('especie'), 'especie');
    llenar(campo('lote'), 'lote');
    const normal = texto => texto.toLowerCase();
    const filtrar = () => {
        const q = normal(campo('q').value.trim());
        let visibles = 0;
        tarjetas.forEach(t => {
            const coincide = (!campo('especie').value || t.dataset.especie === campo('especie').value)
                && (!campo('lote').value || t.dataset.lote === campo('lote').value)
                && (!q || normal(`caja #${t.querySelector('input').value} ${t.dataset.lote} ${t.dataset.camara} ${t.dataset.especie}`).includes(q));
            t.style.display = coincide ? '' : 'none';
            if (coincide) visibles += 1;
        });
        panel.querySelector('[data-conteo]').textContent = `Mostrando ${visibles} de ${tarjetas.length} cajas.`;
    };
    panel.addEventListener('input', filtrar);
    panel.querySelector('[data-accion="marcar"]').addEventListener('click', () => {
        tarjetas.filter(t => t.style.display !== 'none').forEach(t => { t.querySelector('input').checked = true; });
        actualizar();
    });
    panel.querySelector('[data-accion="limpiar"]').addEventListener('click', () => {
        tarjetas.forEach(t => { t.querySelector('input').checked = false; });
        actualizar();
    });
    filtrar();
})();
