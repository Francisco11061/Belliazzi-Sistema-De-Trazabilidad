(() => {
    'use strict';
    const pending = new Map();
    function restore(form) {
        const state = pending.get(form);
        if (!state) return;
        clearTimeout(state.timer);
        form.removeAttribute('aria-busy');
        for (const [button, disabled] of state.buttons) {
            if (disabled === null) button.removeAttribute('aria-disabled');
            else button.setAttribute('aria-disabled', disabled);
        }
        if (state.submitter) {
            state.submitter.innerHTML = state.html;
            if (state.style === null) state.submitter.removeAttribute('style');
            else state.submitter.setAttribute('style', state.style);
            state.submitter.classList.remove('is-submitting');
        }
        state.status.remove();
        pending.delete(form);
    }
    // Bubble phase: valid native submissions only, after the form's own handlers.
    document.addEventListener('submit', event => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post' || form.hasAttribute('data-no-loading')) return;
        if (pending.has(form)) { event.preventDefault(); return; }
        if (event.defaultPrevented) return;
        const submitter = event.submitter;
        const button = submitter?.tagName === 'BUTTON' ? submitter : null;
        const text = submitter?.dataset.loadingText || form.dataset.loadingText || 'Procesando…';
        const status = document.createElement('span');
        status.className = 'form-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        form.append(status);
        const buttons = [...form.querySelectorAll('button:not([type]),button[type="submit"],input[type="submit"]')].map(b => [b, b.getAttribute('aria-disabled')]);
        const state = {submitter: button, html: button?.innerHTML, style: button?.getAttribute('style'), buttons, status};
        pending.set(form, state);
        form.setAttribute('aria-busy', 'true');
        buttons.forEach(([b]) => b.setAttribute('aria-disabled', 'true'));
        // Never disable controls or change name/value: native submitter data stays intact.
        if (button) {
            const rect = button.getBoundingClientRect();
            button.style.width = `${rect.width}px`; button.style.height = `${rect.height}px`;
        }
        state.timer = setTimeout(() => {
            status.textContent = text;
            if (!button) return;
            button.classList.add('is-submitting');
            const spinner = document.createElement('span'); spinner.className = 'form-spinner'; spinner.setAttribute('aria-hidden', 'true');
            const label = document.createElement('span'); label.className = 'form-loading-label'; label.textContent = text;
            button.replaceChildren(spinner, label);
        }, 180);
        // A later submit listener may still cancel the native navigation.
        setTimeout(() => { if (event.defaultPrevented) restore(form); }, 0);
    });
    window.addEventListener('pageshow', () => [...pending.keys()].forEach(restore));
    document.querySelectorAll('[data-error-summary]').forEach(summary => {
        summary.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener('click', event => {
            const field = document.getElementById(link.getAttribute('href').slice(1));
            if (!field) return;
            event.preventDefault();
            let ancestor = field.parentElement;
            while (ancestor) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
            field.focus(); field.scrollIntoView({block:'center'});
        }));
    });
    const first = document.querySelector('[data-error-summary]');
    if (first) first.focus();
})();
