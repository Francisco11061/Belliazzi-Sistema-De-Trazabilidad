(() => {
    'use strict';
    const source = document.getElementById('dashboard-datos');
    if (!source || typeof window.Chart !== 'function') return;
    let data;
    try { data = JSON.parse(source.textContent); } catch { return; }
    const number = new Intl.NumberFormat('es-CL', {maximumFractionDigits: 2});
    for (const name of ['despachos', 'especies']) {
        const canvas = document.getElementById(`${name}-chart`);
        if (!canvas || !data[name]?.labels.length) continue;
        const container = document.getElementById(`${name}-contenedor`);
        const details = document.getElementById(`${name}-datos`);
        const fallback = document.getElementById(`${name}-fallback`);
        try {
            container.hidden = false;
            new Chart(canvas, {
                type: 'bar',
                data: {
                    labels: data[name].labels,
                    datasets: [{label: 'Kg reales conocidos', data: data[name].valores.map(Number),
                        backgroundColor: name === 'especies' ? '#38bdf8' : '#0e7490', borderRadius: 4}]
                },
                options: {
                    indexAxis: name === 'especies' ? 'y' : 'x',
                    responsive: true, maintainAspectRatio: false, animation: false,
                    plugins: {
                        legend: {display: false},
                        tooltip: {callbacks: {label: context => `${number.format(Number(context.raw))} kg`}}
                    },
                    scales: {
                        x: {beginAtZero: true, grid: {display: name === 'especies'}, ticks: {maxRotation: 0, autoSkip: true}},
                        y: {beginAtZero: true, grid: {display: name !== 'especies'}}
                    }
                }
            });
            details.open = false;
            fallback.hidden = true;
        } catch {
            window.Chart.getChart?.(canvas)?.destroy();
            container.hidden = true;
            details.open = true;
            fallback.hidden = false;
            fallback.textContent = 'No se pudo mostrar el gráfico. Consulte los datos a continuación.';
        }
    }
})();
