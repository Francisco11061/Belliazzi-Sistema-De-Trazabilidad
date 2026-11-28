(() => {
    'use strict';
    const source = document.getElementById('dashboard-datos');
    if (!source || typeof window.Chart !== 'function') return;
    let data;
    try { data = JSON.parse(source.textContent); } catch { return; }
    const number = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 2 });
    const palette = ['#0072b2', '#d97706', '#008066', '#a85595', '#6552aa', '#b54d35'];
    const speciesOptions = Array.from(document.querySelectorAll('#id_especie option'))
        .filter(option => option.value).sort((a, b) => Number(a.value) - Number(b.value));
    const labels = new Set([...speciesOptions.map(option => option.textContent.trim()),
        ...(data.especies?.labels ?? []), ...(data.ranking?.labels ?? []),
        ...(data.camaras?.series ?? []).map(s => s.nombre), ...(data.despachos?.series ?? []).map(s => s.nombre)]);
    const speciesColors = new Map([...labels].filter(label => label !== 'Otras').map((label, i) =>
        [label, palette[i] ?? `hsl(${(i * 137.5) % 360} 50% 38%)`]));
    speciesColors.set('Otras', '#64748b');
    for (const name of ['especies', 'ranking', 'camaras', 'despachos']) {
        const canvas = document.getElementById(`${name}-chart`);
        const item = data[name];
        if (!canvas || !item?.labels.length) continue;
        const container = document.getElementById(`${name}-contenedor`);
        const details = document.getElementById(`${name}-datos`);
        const fallback = document.getElementById(`${name}-fallback`);
        const doughnut = name === 'especies', horizontal = ['ranking', 'camaras'].includes(name);
        const weights = series => series.valores.map((value, i) =>
            series.sin_peso?.[i] && series.cajas?.[i] === series.sin_peso[i] ? null : Number(value));
        const values = weights(item);
        const stacked = Boolean(item.series?.length);
        const datasets = stacked ? item.series.map(series => ({
            label: series.nombre, data: weights(series), backgroundColor: speciesColors.get(series.nombre),
            borderWidth: 0, maxBarThickness: 28
        })) : [{
            label: 'Kg netos documentados', data: values,
            backgroundColor: item.labels.map(label => speciesColors.get(label)),
            borderColor: '#fff', borderWidth: doughnut ? 3 : 0, borderRadius: doughnut ? 0 : 4, maxBarThickness: 28
        }];
        const total = values.reduce((a, b) => a + (b ?? 0), 0);
        try {
            container.hidden = false;
            if (horizontal) container.style.height = `${Math.max(180, item.labels.length * 35 + 60)}px`;
            new Chart(canvas, {
                type: doughnut ? 'doughnut' : 'bar',
                plugins: doughnut ? [{
                    id: 'stockTotal',
                    afterDatasetsDraw(chart) {
                        const arc = chart.getDatasetMeta(0).data[0];
                        if (!arc || arc.innerRadius < 32) return;
                        const visible = values.reduce((sum, value, i) => sum + (chart.getDataVisibility(i) ? value ?? 0 : 0), 0);
                        const { ctx } = chart;
                        const width = arc.innerRadius * 1.65;
                        ctx.save();
                        ctx.textAlign = 'center';
                        ctx.fillStyle = '#0f172a';
                        ctx.font = '600 22px system-ui';
                        ctx.fillText(number.format(visible), arc.x, arc.y - 1, width);
                        ctx.fillStyle = '#475569';
                        ctx.font = '12px system-ui';
                        ctx.fillText('kg en stock', arc.x, arc.y + 17, width);
                        ctx.restore();
                    }
                }] : [],
                data: { labels: item.labels, datasets },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    indexAxis: horizontal ? 'y' : 'x', cutout: doughnut ? '70%' : undefined,
                    plugins: {
                        legend: { display: doughnut || stacked, position: 'bottom', labels: { color: '#334155', usePointStyle: true, pointStyle: 'circle', boxWidth: 10, padding: 12,
                            font: { size: 11 } } },
                        tooltip: { backgroundColor: '#0f172a', padding: 12, callbacks: {
                            label: context => {
                                const kg = `${number.format(Number(context.raw))} kg`;
                                if (stacked) return `${context.dataset.label}: ${kg}`;
                                return doughnut ? `${kg} (${number.format(total ? Number(context.raw) / total * 100 : 0)}% del peso conocido)` : kg;
                            },
                            afterLabel: context => {
                                const lines = [], i = context.dataIndex;
                                const series = stacked ? item.series[context.datasetIndex] : item;
                                if (series.cajas) lines.push(`${number.format(series.cajas[i])} cajas`);
                                if (series.sin_peso?.[i]) lines.push(`${series.sin_peso[i]} cajas sin peso: total parcial`);
                                return lines;
                            }
                        } }
                    },
                    scales: doughnut ? {} : {
                        x: { stacked, beginAtZero: true, grid: { display: horizontal, color: '#f1f5f9' }, border: { display: false },
                            ticks: { color: '#475569', maxRotation: 0, autoSkip: true, maxTicksLimit: horizontal ? 5 : 7,
                                callback: horizontal ? value => number.format(value) : function(value) { return this.getLabelForValue(value); } } },
                        y: { stacked, beginAtZero: true, grid: { display: !horizontal, color: '#f1f5f9' }, border: { display: false },
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
