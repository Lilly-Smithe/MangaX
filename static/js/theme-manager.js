/* Reader ve Full tarafından paylaşılan, ilk çizimden önce çalışan tema yöneticisi. */
(function initializeMangaXTheme(global) {
    'use strict';

    const STORAGE_KEY = 'mangax-app-theme-v1';
    const THEMES = Object.freeze(['dark', 'light', 'cover_grid', 'windows_xp', 'pornhub']);
    const PORNHUB_UNLOCK_CLICK_COUNT = 10;
    const PORNHUB_UNLOCK_WINDOW_MS = 4000;
    const root = document.documentElement;
    let pornhubThemeUnlocked = false;
    let appearanceClicks = [];

    function normalize(theme) {
        return THEMES.includes(String(theme || '').toLowerCase())
            ? String(theme).toLowerCase()
            : 'dark';
    }

    function readCachedTheme() {
        const serverTheme = String(root.dataset.theme || '').toLowerCase();
        if (THEMES.includes(serverTheme)) return serverTheme;
        try {
            return normalize(localStorage.getItem(STORAGE_KEY));
        } catch (_) {
            return 'dark';
        }
    }

    function cacheTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (_) { /* Salt okunur depolamada tema bu oturumda yine uygulanır. */ }
    }

    function updateControls(theme) {
        document.querySelectorAll('[data-app-theme-option]').forEach(control => {
            const selected = control.dataset.appThemeOption === theme;
            control.classList.toggle('selected', selected);
            control.setAttribute('aria-checked', String(selected));
            const indicator = control.querySelector('.theme-option-indicator');
            if (indicator) indicator.setAttribute('aria-hidden', String(!selected));
        });
        const status = document.getElementById('app-theme-status');
        if (status) {
            const labels = {
                dark: 'MangaX Koyu etkin',
                light: 'MangaX Açık etkin',
                cover_grid: 'MangaX Grid etkin',
                windows_xp: 'MangaX XP etkin',
                pornhub: 'Pornhub teması etkin',
            };
            status.textContent = labels[theme] || labels.dark;
        }
    }

    function apply(theme, options = {}) {
        const normalized = normalize(theme);
        root.dataset.theme = normalized;
        if (options.persist !== false) cacheTheme(normalized);
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => updateControls(normalized), { once: true });
        } else {
            updateControls(normalized);
        }
        global.dispatchEvent(new CustomEvent('mangax:theme-change', { detail: { theme: normalized } }));
        return normalized;
    }

    async function selectTheme(theme) {
        const previous = normalize(root.dataset.theme);
        const selected = apply(theme);
        try {
            const response = await fetch('/api/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ app_theme: selected }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || 'Tema tercihi kaydedilemedi.');
            syncFromPreferences(result.settings?.app_theme || selected);
            if (typeof global.showToast === 'function') global.showToast('Uygulama teması kaydedildi.', 'success');
            return selected;
        } catch (error) {
            apply(previous);
            if (typeof global.showToast === 'function') global.showToast(error.message || 'Tema tercihi kaydedilemedi.', 'error');
            return previous;
        }
    }

    function syncFromPreferences(theme) {
        return apply(normalize(theme));
    }

    function renderPornhubUnlockState() {
        const option = document.querySelector('[data-app-theme-option="pornhub"]');
        if (option) option.hidden = !pornhubThemeUnlocked;
    }

    function syncUnlockFromPreferences(unlocked) {
        pornhubThemeUnlocked = pornhubThemeUnlocked || unlocked === true;
        renderPornhubUnlockState();
        return pornhubThemeUnlocked;
    }

    async function persistPornhubUnlock() {
        try {
            const response = await fetch('/api/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pornhub_theme_unlocked: true }),
            });
            const result = await response.json();
            if (!response.ok || result.settings?.pornhub_theme_unlocked !== true) {
                throw new Error(result.detail || 'Gizli tema kaydedilemedi.');
            }
        } catch (error) {
            if (typeof global.showToast === 'function') {
                global.showToast(error.message || 'Gizli tema tercihi kaydedilemedi.', 'error');
            }
        }
    }

    function handleAppearanceUnlockClick() {
        if (pornhubThemeUnlocked) return;
        const now = Date.now();
        appearanceClicks = appearanceClicks.filter(timestamp => now - timestamp <= PORNHUB_UNLOCK_WINDOW_MS);
        appearanceClicks.push(now);
        if (appearanceClicks.length < PORNHUB_UNLOCK_CLICK_COUNT) return;
        appearanceClicks = [];
        pornhubThemeUnlocked = true;
        renderPornhubUnlockState();
        persistPornhubUnlock();
        if (typeof global.showToast === 'function') global.showToast('Gizli tema açıldı.', 'success');
    }

    function bindPornhubUnlockGesture() {
        renderPornhubUnlockState();
        document.querySelector('[data-settings-category="appearance"]')
            ?.addEventListener('click', handleAppearanceUnlockClick);
    }

    const initialTheme = apply(readCachedTheme(), { persist: false });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindPornhubUnlockGesture, { once: true });
    } else {
        bindPornhubUnlockGesture();
    }
    global.MangaXTheme = Object.freeze({
        STORAGE_KEY,
        THEMES,
        normalize,
        apply,
        selectTheme,
        syncFromPreferences,
        syncUnlockFromPreferences,
        getTheme: () => normalize(root.dataset.theme || initialTheme),
    });
})(window);
