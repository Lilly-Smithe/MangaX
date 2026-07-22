/* Reader ve Full tarafından paylaşılan, ilk çizimden önce çalışan yerleşim yöneticisi. */
(function initializeMangaXLayout(global) {
    'use strict';

    const STORAGE_KEY = 'mangax-layout-preferences-v1';
    const LEGACY_COLLAPSED_KEY = 'mangax-sidebar-collapsed-v1';
    const DEFAULTS = Object.freeze({
        nav_position: 'left',
        nav_mode: 'wide',
        nav_auto_hide: false,
        library_density: 'balanced',
        reduce_motion: false,
        ui_scale: 'normal',
    });
    const ALLOWED = Object.freeze({
        nav_position: Object.freeze(['left', 'right', 'top', 'bottom']),
        nav_mode: Object.freeze(['wide', 'compact', 'icons']),
        library_density: Object.freeze(['comfortable', 'balanced', 'dense']),
        ui_scale: Object.freeze(['small', 'normal', 'large']),
    });
    const root = document.documentElement;
    let state = { ...DEFAULTS };
    let controlsBound = false;

    function normalize(input = {}) {
        const normalized = { ...DEFAULTS };
        Object.keys(ALLOWED).forEach(key => {
            const value = String(input[key] || '').toLowerCase();
            normalized[key] = ALLOWED[key].includes(value) ? value : DEFAULTS[key];
        });
        normalized.nav_auto_hide = input.nav_auto_hide === true;
        normalized.reduce_motion = input.reduce_motion === true;
        return normalized;
    }

    function readCache() {
        try {
            const cached = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
            if (cached && typeof cached === 'object') return normalize(cached);
            if (localStorage.getItem(LEGACY_COLLAPSED_KEY) === 'true') {
                return normalize({ ...DEFAULTS, nav_mode: 'icons' });
            }
        } catch (_) { /* Salt okunur depolamada güvenli varsayılanlar kullanılır. */ }
        return { ...DEFAULTS };
    }

    function writeCache(value) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
            localStorage.removeItem(LEGACY_COLLAPSED_KEY);
        } catch (_) { /* Yerleşim bu oturumda uygulanmaya devam eder. */ }
    }

    function updateCollapseControl() {
        const button = document.getElementById('sidebar-collapse-btn');
        if (!button) return;
        const collapsed = state.nav_mode === 'icons';
        const horizontal = ['top', 'bottom'].includes(state.nav_position);
        const positionLabels = { left: 'sol', right: 'sağ', top: 'üst', bottom: 'alt' };
        button.setAttribute('aria-expanded', String(!collapsed));
        button.setAttribute('aria-label', collapsed ? 'Navigasyonu genişlet' : 'Navigasyonu daralt');
        button.title = collapsed ? 'Navigasyonu genişlet' : 'Navigasyonu daralt';
        const icon = button.querySelector('i');
        if (icon) {
            const expandedIcon = horizontal ? 'fa-compress' : (state.nav_position === 'right' ? 'fa-angles-right' : 'fa-angles-left');
            const collapsedIcon = horizontal ? 'fa-expand' : (state.nav_position === 'right' ? 'fa-angles-left' : 'fa-angles-right');
            icon.className = `fa-solid ${collapsed ? collapsedIcon : expandedIcon}`;
        }
        button.dataset.positionLabel = positionLabels[state.nav_position];
    }

    function updateControls() {
        document.querySelectorAll('[data-layout-key][data-layout-value]').forEach(control => {
            const key = control.dataset.layoutKey;
            const selected = state[key] === control.dataset.layoutValue;
            control.classList.toggle('selected', selected);
            control.setAttribute('aria-checked', String(selected));
        });
        document.querySelectorAll('[data-layout-boolean]').forEach(control => {
            control.checked = state[control.dataset.layoutBoolean] === true;
        });
        document.querySelectorAll('.nav-btn[data-label]').forEach(button => {
            button.title = button.dataset.label;
            if (state.nav_mode === 'icons') button.setAttribute('aria-label', button.dataset.label);
            else button.removeAttribute('aria-label');
        });
        const status = document.getElementById('layout-selection-status');
        if (status) {
            const positions = { left: 'Sol', right: 'Sağ', top: 'Üst', bottom: 'Alt' };
            const modes = { wide: 'geniş', compact: 'kompakt', icons: 'yalnız simge' };
            status.textContent = `Navigasyon: ${positions[state.nav_position]}, ${modes[state.nav_mode]}`;
        }
        updateCollapseControl();
    }

    function apply(next, options = {}) {
        state = normalize({ ...state, ...next });
        root.dataset.navPosition = state.nav_position;
        root.dataset.navMode = state.nav_mode;
        root.dataset.navAutoHide = String(state.nav_auto_hide);
        root.dataset.libraryDensity = state.library_density;
        root.dataset.reduceMotion = String(state.reduce_motion);
        root.dataset.uiScale = state.ui_scale;
        document.getElementById('app-sidebar')?.classList.toggle('sidebar-collapsed', state.nav_mode === 'icons');
        if (options.persist !== false) writeCache(state);
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', updateControls, { once: true });
        } else {
            updateControls();
        }
        global.dispatchEvent(new CustomEvent('mangax:layout-change', { detail: { ...state } }));
        return { ...state };
    }

    async function persistChange(patch) {
        const previous = { ...state };
        const selected = apply(patch);
        try {
            const response = await fetch('/api/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || 'Görünüm tercihi kaydedilemedi.');
            syncFromPreferences(result.settings || selected);
            return { ...state };
        } catch (error) {
            apply(previous);
            if (typeof global.showToast === 'function') {
                global.showToast(error.message || 'Görünüm tercihi kaydedilemedi.', 'error');
            }
            return { ...state };
        }
    }

    function selectOption(key, value) {
        if (!Object.prototype.hasOwnProperty.call(ALLOWED, key)) return Promise.resolve({ ...state });
        return persistChange({ [key]: value });
    }

    function setBoolean(key, value) {
        if (!['nav_auto_hide', 'reduce_motion'].includes(key)) return Promise.resolve({ ...state });
        return persistChange({ [key]: Boolean(value) });
    }

    function toggleCollapsed() {
        return selectOption('nav_mode', state.nav_mode === 'icons' ? 'wide' : 'icons');
    }

    function syncFromPreferences(preferences = {}) {
        return apply({
            nav_position: preferences.nav_position,
            nav_mode: preferences.nav_mode,
            nav_auto_hide: preferences.nav_auto_hide,
            library_density: preferences.library_density,
            reduce_motion: preferences.reduce_motion,
            ui_scale: preferences.ui_scale,
        });
    }

    function bindControls() {
        if (controlsBound) return;
        controlsBound = true;
        document.addEventListener('click', event => {
            const option = event.target.closest('[data-layout-key][data-layout-value]');
            if (!option) return;
            selectOption(option.dataset.layoutKey, option.dataset.layoutValue);
        });
        document.addEventListener('change', event => {
            const control = event.target.closest('[data-layout-boolean]');
            if (control) setBoolean(control.dataset.layoutBoolean, control.checked);
        });
        updateControls();
    }

    apply(readCache(), { persist: false });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindControls, { once: true });
    else bindControls();

    global.MangaXLayout = Object.freeze({
        STORAGE_KEY,
        DEFAULTS,
        ALLOWED,
        normalize,
        apply,
        selectOption,
        setBoolean,
        toggleCollapsed,
        syncFromPreferences,
        getState: () => ({ ...state }),
    });
})(window);
