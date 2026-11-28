// Inicio de planta: se actualiza solo cada pocos minutos, sin recargar ni perder la posición.
(() => {
    const estado = document.querySelector('[data-actualizacion]');
    const lista = document.querySelector('[data-pendientes]');
    if (!estado || !lista) return;
    const texto = estado.querySelector('[data-actualizacion-texto]');
    const punto = estado.querySelector('[data-actualizacion-punto]');
    const cadaMs = Number(estado.dataset.cada || 120) * 1000;
    const formato = new Intl.DateTimeFormat('es-CL', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    let ultima = new Date();
    let ultimaActividad = Date.now();

    const pintarTiempo = (mensaje) => {
        const minutos = Math.floor((Date.now() - ultima.getTime()) / 60000);
        const hace = minutos < 1 ? 'recién' : `hace ${minutos} min`;
        texto.innerHTML = '';
        const time = document.createElement('time');
        time.dateTime = ultima.toISOString();
        time.dataset.tip = formato.format(ultima).replace(',', '');
        time.textContent = mensaje || `Actualizado ${hace}`;
        texto.append(time);
    };

    const actualizar = async () => {
        // No interrumpir a quien está usando la página.
        if (document.hidden || Date.now() - ultimaActividad < 20000) return;
        if (lista.contains(document.activeElement)) return;
        try {
            const respuesta = await fetch(window.location.href, { headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' });
            if (!respuesta.ok || respuesta.redirected) throw new Error(String(respuesta.status));
            const nueva = new DOMParser().parseFromString(await respuesta.text(), 'text/html');
            const nuevaLista = nueva.querySelector('[data-pendientes]');
            if (!nuevaLista) throw new Error('sin lista');
            lista.innerHTML = nuevaLista.innerHTML;
            const resumen = nueva.querySelector('[data-resumen-pendientes]');
            if (resumen) document.querySelector('[data-resumen-pendientes]').textContent = resumen.textContent;
            document.title = nueva.title;
            // Los contadores del menú lateral también cambian.
            const menu = document.querySelector('#sidebar nav'), nuevoMenu = nueva.querySelector('#sidebar nav');
            if (menu && nuevoMenu && !menu.contains(document.activeElement)) menu.innerHTML = nuevoMenu.innerHTML;
            ultima = new Date();
            punto.className = 'size-2 rounded-full bg-emerald-500';
            pintarTiempo();
        } catch (error) {
            punto.className = 'size-2 rounded-full bg-amber-500';
            pintarTiempo('Sin conexión: se reintentará en un momento');
        }
    };

    ['pointerdown', 'keydown', 'scroll'].forEach(evento =>
        window.addEventListener(evento, () => { ultimaActividad = Date.now(); }, { passive: true }));
    document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - ultima.getTime() > cadaMs) actualizar(); });
    setInterval(() => pintarTiempo(), 30000);
    setInterval(actualizar, cadaMs);
})();
