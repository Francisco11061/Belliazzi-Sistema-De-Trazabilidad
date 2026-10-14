(() => {
    'use strict';
    const desktop = window.matchMedia('(min-width: 48rem)');
    document.querySelectorAll('[data-responsive-filters]').forEach(details => {
        const fields = [...details.querySelectorAll('input:not([type=hidden]), select')];
        const count = fields.filter(field => field.value.trim() !== '').length;
        const counter = details.querySelector('[data-filter-count]');
        if (counter) { counter.textContent = `(${count})`; counter.hidden = !count; }
        const update = () => { details.open = desktop.matches || (details.dataset.active ? details.dataset.active === 'true' : count > 0); };
        update();
        desktop.addEventListener('change', update);
    });
    document.addEventListener('click', event => {
        document.querySelectorAll('[data-action-menu][open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
        const row = event.target.closest('[data-row-link]');
        if (!row || event.target.closest('a, button, input, select, textarea, summary, details, label') || window.getSelection().toString()) return;
        const link = row.querySelector('[data-detail-link]');
        if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) window.location.assign(link.href);
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        document.querySelectorAll('[data-action-menu][open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
    });
    document.querySelectorAll('[data-dismiss-message]').forEach(button => {
        button.addEventListener('click', () => {
            const message = button.closest('[data-message]');
            if (message.contains(document.activeElement)) document.getElementById('main-content')?.focus();
            message.remove();
        });
    });
    document.querySelectorAll('[data-auto-dismiss]').forEach(message => {
        window.setTimeout(() => { if (!message.contains(document.activeElement)) message.remove(); }, 6000);
    });
})();
