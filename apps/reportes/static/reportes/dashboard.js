(() => {
    'use strict';
    const filters = document.getElementById('dashboard-filtros');
    if (filters && matchMedia('(max-width: 639px)').matches && !filters.querySelector('[role="alert"]')) filters.open = false;
    const source = document.getElementById('dashboard-datos');
    if (!source || typeof window.Chart !== 'function') return;
    let data;
    try { data = JSON.parse(source.textContent); } catch { return; }
    const number = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 2 });
    const palette = ['#0e7490', '#0369a1', '#0f766e', '#475569', '#7c3aed', '#a16207', '#94a3b8'];
    for (const name of ['especies', 'ranking', 'camaras', 'despachos']) {
        const canvas = document.getElementById(`${name}-chart`);
        const item = data[name];
        if (!canvas || !item?.labels.length) continue;
        const container = document.getElementById(`${name}-contenedor`);
        const details = document.getElementById(`${name}-datos`);
        const fallback = document.getElementById(`${name}-fallback`);
        const doughnut = name === 'especies', horizontal = ['ranking', 'camaras'].includes(name);
        const values = item.valores.map((value, i) => item.sin_peso?.[i] && item.cajas?.[i] === item.sin_peso[i] ? null : Number(value));
        const total = values.reduce((a, b) => a + (b ?? 0), 0);
        try {
            container.hidden = false;
            if (horizontal) container.style.height = `${Math.max(180, item.labels.length * 35 + 60)}px`;
            new Chart(canvas, {
                type: doughnut ? 'doughnut' : 'bar',
                data: { labels: item.labels, datasets: [{ label: 'Kg netos documentados', data: values,
                    backgroundColor: doughnut ? palette : '#0e7490', borderWidth: doughnut ? 3 : 0,
                    borderColor: '#fff', borderRadius: doughnut ? 0 : 4, maxBarThickness: 28 }] },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    indexAxis: horizontal ? 'y' : 'x', cutout: doughnut ? '70%' : undefined,
                    plugins: {
                        legend: { display: doughnut, position: 'bottom', labels: { color: '#475569', boxWidth: 10, padding: 12,
                            font: { size: 11 } } },
                        tooltip: { backgroundColor: '#0f172a', padding: 12, callbacks: {
                            label: context => {
                                const kg = `${number.format(Number(context.raw))} kg`;
                                return doughnut ? `${kg} (${number.format(total ? Number(context.raw) / total * 100 : 0)}% del peso conocido)` : kg;
                            },
                            afterLabel: context => {
                                const lines = [], i = context.dataIndex;
                                if (item.cajas) lines.push(`${number.format(item.cajas[i])} cajas`);
                                if (item.sin_peso?.[i]) lines.push(`${item.sin_peso[i]} cajas sin peso: total parcial`);
                                return lines;
                            }
                        } }
                    },
                    scales: doughnut ? {} : {
                        x: { beginAtZero: true, grid: { display: horizontal, color: '#f1f5f9' }, border: { display: false },
                            ticks: { color: '#475569', maxRotation: 0, autoSkip: true, maxTicksLimit: horizontal ? 5 : 7,
                                callback: horizontal ? value => number.format(value) : undefined } },
                        y: { beginAtZero: true, grid: { display: !horizontal, color: '#f1f5f9' }, border: { display: false },
                            ticks: { color: '#475569', font: { size: 11 },
                                callback: horizontal ? function(value) { const text = this.getLabelForValue(value); return text.length > 23 ? text.slice(0, 21) + '…' : text; } : value => number.format(value) } }
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
            fallback.textContent = 'No se pudo mostrar el gráfico. Consulta los datos a continuación.';
        }
    }
})();
