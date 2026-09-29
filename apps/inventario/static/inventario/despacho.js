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
})();
