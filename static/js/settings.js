const ADVANCED_CLIENT_SETTINGS_KEY = 'mangax-advanced-settings-v1';
const NOTIFICATION_PREFERENCES_KEY = 'mangax-notification-preferences-v1';
let advancedSettingsState = {};
let sourcePriorityState = [];

function readLocalJson(key, fallback = {}) {
    try { return JSON.parse(localStorage.getItem(key) || 'null') || fallback; } catch (_) { return fallback; }
}

function getAppPreference(key, fallback = null) {
    const stored = readLocalJson(ADVANCED_CLIENT_SETTINGS_KEY, {});
    return Object.prototype.hasOwnProperty.call(stored, key) ? stored[key] : fallback;
}

function getNotificationPreference(key, fallback = true) {
    const stored = readLocalJson(NOTIFICATION_PREFERENCES_KEY, {});
    return Object.prototype.hasOwnProperty.call(stored, key) ? Boolean(stored[key]) : fallback;
}

function isNotificationQuietHours() {
    const preferences = readLocalJson(NOTIFICATION_PREFERENCES_KEY, {});
    if (!preferences.quietStart || !preferences.quietEnd || preferences.quietStart === preferences.quietEnd) return false;
    const now = new Date();
    const current = now.getHours() * 60 + now.getMinutes();
    const toMinutes = value => {
        const [hours, minutes] = String(value).split(':').map(Number);
        return hours * 60 + minutes;
    };
    const start = toMinutes(preferences.quietStart);
    const end = toMinutes(preferences.quietEnd);
    return start < end ? current >= start && current < end : current >= start || current < end;
}

function shouldShowNotification(kind) {
    return getNotificationPreference(kind) && !isNotificationQuietHours();
}

const SETTINGS_CATEGORIES = ['general', 'reader', 'backup', 'integrations', 'system'];
const GITHUB_SETTINGS_CATEGORIES = new Set(['general', 'sources', 'downloads', 'notifications']);

function settingsFullFeaturesAvailable() {
    return typeof isGithubExtensionsAvailable === 'function'
        ? isGithubExtensionsAvailable()
        : !(typeof isReaderEdition === 'function' && isReaderEdition());
}

function getSavedSettingsCategory() {
    try {
        const saved = localStorage.getItem(SETTINGS_CATEGORY_STORAGE_KEY);
        if (GITHUB_SETTINGS_CATEGORIES.has(saved) && !settingsFullFeaturesAvailable()) return 'reader';
        return SETTINGS_CATEGORIES.includes(saved) ? saved : (settingsFullFeaturesAvailable() ? 'general' : 'reader');
    } catch (_) {
        return settingsFullFeaturesAvailable() ? 'general' : 'reader';
    }
}

function switchSettingsCategory(category) {
    const requested = GITHUB_SETTINGS_CATEGORIES.has(category) && !settingsFullFeaturesAvailable() ? 'reader' : category;
    const normalized = SETTINGS_CATEGORIES.includes(requested)
        ? requested
        : 'general';
    document.querySelectorAll('[data-settings-category]').forEach(button => {
        const active = button.dataset.settingsCategory === normalized;
        button.classList.toggle('active', active);
        button.setAttribute('aria-current', active ? 'page' : 'false');
    });
    document.querySelectorAll('[data-settings-panel]').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.settingsPanel === normalized);
    });
    try {
        localStorage.setItem(SETTINGS_CATEGORY_STORAGE_KEY, normalized);
    } catch (_) { /* kategori bu oturumda görünür kalır */ }
    if (normalized === 'integrations' && typeof loadMalIntegrationStatus === 'function') {
        loadMalIntegrationStatus();
        if (typeof loadGithubIntegrationStatus === 'function') loadGithubIntegrationStatus();
    }
}

function loadSettingsCategory() {
    switchSettingsCategory(getSavedSettingsCategory());
    loadAdvancedPreferences();
}

function setControlValue(id, value, checked = false) {
    const control = document.getElementById(id);
    if (!control) return;
    if (checked) control.checked = Boolean(value);
    else control.value = value ?? '';
}

async function loadAdvancedPreferences() {
    try {
        const response = await fetch('/api/preferences', { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        advancedSettingsState = data.settings || {};
        sourcePriorityState = data.source_priority || [];
        localStorage.setItem(ADVANCED_CLIENT_SETTINGS_KEY, JSON.stringify({ ...advancedSettingsState, source_priority: sourcePriorityState }));
        setControlValue('settings-fallback-mode', advancedSettingsState.fallback_mode || 'ask');
        setControlValue('settings-catalog-provider', advancedSettingsState.catalog_provider_preference || 'anilist');
        setControlValue('settings-extension-update-mode', advancedSettingsState.extension_update_mode || 'notify');
        setControlValue('settings-backup-before-update', advancedSettingsState.backup_before_extension_update, true);
        setControlValue('settings-request-timeout', advancedSettingsState.request_timeout_seconds || 15);
        setControlValue('settings-download-concurrency', advancedSettingsState.download_concurrency || 3);
        setControlValue('settings-cache-limit', advancedSettingsState.image_cache_limit_mb || 512);
        setControlValue('settings-low-bandwidth', advancedSettingsState.low_bandwidth_mode, true);
        setControlValue('settings-download-directory', advancedSettingsState.download_directory || data.storage?.downloads_directory || '');
        setControlValue('settings-safe-mode', advancedSettingsState.safe_mode, true);
        setControlValue('settings-automatic-update-checks', advancedSettingsState.automatic_update_checks !== false, true);
        const lastUpdateCheck = document.getElementById('app-update-last-check');
        if (lastUpdateCheck && advancedSettingsState.last_app_update_check) {
            lastUpdateCheck.textContent = new Date(advancedSettingsState.last_app_update_check).toLocaleString('tr-TR');
        }
        renderStorageOverview(data.storage || {});
    } catch (error) {
        console.warn('Gelişmiş ayarlar yüklenemedi:', error);
    }
    syncReaderDefaultsToSettings();
    syncClientPreferenceControls();
}

function advancedPreferencesFromControls() {
    return {
        fallback_mode: document.getElementById('settings-fallback-mode')?.value || 'ask',
        catalog_provider_preference: document.getElementById('settings-catalog-provider')?.value || undefined,
        extension_update_mode: document.getElementById('settings-extension-update-mode')?.value || 'notify',
        backup_before_extension_update: Boolean(document.getElementById('settings-backup-before-update')?.checked),
        request_timeout_seconds: Number(document.getElementById('settings-request-timeout')?.value) || 15,
        download_concurrency: Number(document.getElementById('settings-download-concurrency')?.value) || 3,
        image_cache_limit_mb: Number(document.getElementById('settings-cache-limit')?.value) || 512,
        low_bandwidth_mode: Boolean(document.getElementById('settings-low-bandwidth')?.checked),
        download_directory: document.getElementById('settings-download-directory')?.value || '',
        safe_mode: Boolean(document.getElementById('settings-safe-mode')?.checked),
        automatic_update_checks: document.getElementById('settings-automatic-update-checks')?.checked !== false,
        source_priority: sourcePriorityState,
    };
}

async function saveAdvancedPreferences() {
    const payload = advancedPreferencesFromControls();
    const previousCatalogProvider = advancedSettingsState.catalog_provider_preference || 'anilist';
    if (payload.low_bandwidth_mode) {
        payload.download_concurrency = Math.min(payload.download_concurrency, 2);
        localStorage.setItem('downloadCompressionProfile', 'compact');
        loadDownloadCompressionSetting();
    }
    try {
        const response = await fetch('/api/preferences', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Ayarlar kaydedilemedi.');
        advancedSettingsState = data.settings || payload;
        localStorage.setItem(ADVANCED_CLIENT_SETTINGS_KEY, JSON.stringify({ ...advancedSettingsState, source_priority: sourcePriorityState }));
        if (
            payload.catalog_provider_preference
            && payload.catalog_provider_preference !== previousCatalogProvider
            && typeof cancelDiscoverRequests === 'function'
        ) {
            cancelDiscoverRequests();
        }
        showToast('Ayar kaydedildi.', 'success');
    } catch (error) {
        setControlValue('settings-catalog-provider', previousCatalogProvider);
        showToast(error.message, 'error');
    }
}





function renderStorageOverview(storage) {
    const target = document.getElementById('settings-storage-overview');
    if (!target) return;
    const rows = (storage.mangas || []).slice(0, 8);
    const { element, text } = window.MangaXSafeDOM;
    const metric = (value, label) => element('span', {}, [element('strong', { text: value }), text(` ${label}`)]);
    target.replaceChildren(
        element('div', { className: 'storage-summary' }, [metric(formatStorageBytes(storage.total_download_bytes || 0), 'İndirilenler'), metric(formatStorageBytes(storage.cache_bytes || 0), 'Önbellek')]),
        ...rows.map(item => element('div', { className: 'storage-manga-row' }, [element('span', { text: item.title || '' }), element('strong', { text: formatStorageBytes(item.bytes) })])),
    );
}

async function clearApplicationCache() {
    const response = await fetch('/api/preferences/cache/clear', { method: 'POST' });
    if (!response.ok) return showToast('Önbellek temizlenemedi.', 'error');
    const data = await response.json();
    showToast(`${formatStorageBytes(data.cleared_bytes || 0)} önbellek temizlendi.`, 'success');
    loadAdvancedPreferences();
}

async function cleanupStaleDownloads() {
    if (!await showAppConfirm({ title: 'Eski İndirmeleri Temizle', message: '30 gündür okunmayan veya güncellenmeyen mangaların indirilen sayfaları silinecek. Kütüphane ve okuma geçmişi korunur.', confirmText: 'İndirmeleri Temizle', variant: 'danger', icon: 'fa-trash-can' })) return;
    const response = await fetch('/api/preferences/storage/cleanup-stale', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) return showToast(data.detail || 'Temizlik tamamlanamadı.', 'error');
    showToast(`${data.removed_chapters || 0} bölüm temizlendi; ${formatStorageBytes(data.freed_bytes || 0)} boşaltıldı.`, 'success');
    await Promise.all([loadLibrary(), loadAdvancedPreferences()]);
}

function syncReaderDefaultsToSettings() {
    if (typeof readerPreferences === 'undefined') return;
    ['mode', 'spread', 'fit', 'background', 'zoom', 'brightness'].forEach(key => setControlValue(`settings-reader-${key}`, readerPreferences[key]));
    setControlValue('settings-reader-auto-next', readerPreferences.autoNext, true);
}

let readerSettingsPreviewOpen = false;
let readerSettingsPreviewPage = 0;
let readerSettingsSavedTimer = null;

function readerDefaultsFromSettings() {
    return {
        mode: document.getElementById('settings-reader-mode')?.value || 'webtoon',
        spread: document.getElementById('settings-reader-spread')?.value || 'single',
        fit: document.getElementById('settings-reader-fit')?.value || 'page',
        background: document.getElementById('settings-reader-background')?.value || 'black',
        zoom: Number(document.getElementById('settings-reader-zoom')?.value) || 100,
        brightness: Number(document.getElementById('settings-reader-brightness')?.value) || 100,
        autoNext: Boolean(document.getElementById('settings-reader-auto-next')?.checked),
    };
}

function saveReaderDefaultsFromSettings() {
    if (typeof saveReaderPreferenceDefaults !== 'function') return;
    saveReaderPreferenceDefaults(readerDefaultsFromSettings());
    renderReaderSettingsPreview();
    const state = document.getElementById('reader-settings-save-state');
    if (state) {
        state.innerHTML = '<i class="fa-solid fa-check"></i> Kaydedildi';
        clearTimeout(readerSettingsSavedTimer);
        readerSettingsSavedTimer = setTimeout(() => { state.textContent = ''; }, 1600);
    }
}

function resetReaderDefaultsFromSettings() {
    if (typeof saveReaderPreferenceDefaults !== 'function') return;
    saveReaderPreferenceDefaults({ ...DEFAULT_READER_PREFERENCES });
    syncReaderDefaultsToSettings();
    renderReaderSettingsPreview();
    showToast('Okuyucu varsayılanları sıfırlandı.', 'success');
}

function toggleReaderSettingsPreview(force) {
    readerSettingsPreviewOpen = typeof force === 'boolean' ? force : !readerSettingsPreviewOpen;
    const workspace = document.getElementById('reader-settings-workspace');
    const preview = document.getElementById('reader-settings-preview');
    const button = document.getElementById('reader-preview-toggle');
    workspace?.classList.toggle('preview-active', readerSettingsPreviewOpen);
    preview?.setAttribute('aria-hidden', String(!readerSettingsPreviewOpen));
    if (button) {
        button.classList.toggle('active', readerSettingsPreviewOpen);
        button.innerHTML = readerSettingsPreviewOpen
            ? '<i class="fa-solid fa-eye-slash"></i> Canlı Önizlemeyi Kapat'
            : '<i class="fa-solid fa-eye"></i> Canlı Önizlemeyi Aç';
    }
    if (readerSettingsPreviewOpen) renderReaderSettingsPreview();
}

function renderReaderSettingsPreview() {
    if (!readerSettingsPreviewOpen) return;
    const values = readerDefaultsFromSettings();
    const stage = document.getElementById('reader-preview-stage');
    if (!stage) return;
    stage.dataset.mode = values.mode;
    stage.dataset.spread = values.spread;
    stage.dataset.fit = values.fit;
    stage.dataset.background = values.background;
    stage.style.setProperty('--preview-zoom', String(values.zoom / 100));
    stage.style.setProperty('--preview-brightness', String(values.brightness / 100));
    const modeLabel = document.getElementById('reader-preview-mode-label');
    if (modeLabel) modeLabel.textContent = `${values.mode === 'classic' ? 'Klasik' : 'Webtoon'} · ${values.spread === 'double' ? 'Çift sayfa' : 'Tek sayfa'}`;
    const pages = [...stage.querySelectorAll('[data-preview-page]')];
    if (values.mode === 'classic' && values.spread === 'double') readerSettingsPreviewPage = Math.floor(readerSettingsPreviewPage / 2) * 2;
    readerSettingsPreviewPage = Math.max(0, Math.min(pages.length - 1, readerSettingsPreviewPage));
    pages.forEach((page, index) => {
        const visible = values.mode === 'webtoon'
            || index === readerSettingsPreviewPage
            || (values.spread === 'double' && index === readerSettingsPreviewPage + 1);
        page.classList.toggle('is-preview-visible', visible);
    });
    const navigation = document.querySelector('.reader-preview-navigation');
    navigation?.classList.toggle('webtoon-navigation', values.mode === 'webtoon');
    const pageLabel = document.getElementById('reader-preview-page-label');
    if (pageLabel) pageLabel.textContent = values.mode === 'webtoon' ? '3 örnek sayfa' : `${readerSettingsPreviewPage + 1} / ${pages.length}`;
}

function stepReaderSettingsPreview(direction) {
    const values = readerDefaultsFromSettings();
    if (values.mode === 'webtoon') return;
    const step = values.spread === 'double' ? 2 : 1;
    readerSettingsPreviewPage = Math.max(0, Math.min(2, readerSettingsPreviewPage + direction * step));
    renderReaderSettingsPreview();
}

function syncClientPreferenceControls() {
    setControlValue('settings-notify-chapters', getNotificationPreference('chapters'), true);
    setControlValue('settings-notify-extensions', getNotificationPreference('extensions'), true);
    setControlValue('settings-notify-downloads', getNotificationPreference('downloads'), true);
    const prefs = readLocalJson(NOTIFICATION_PREFERENCES_KEY, {});
    setControlValue('settings-quiet-start', prefs.quietStart || '');
    setControlValue('settings-quiet-end', prefs.quietEnd || '');
}

function saveClientPreferenceControls() {
    const values = { chapters: Boolean(document.getElementById('settings-notify-chapters')?.checked), extensions: Boolean(document.getElementById('settings-notify-extensions')?.checked), downloads: Boolean(document.getElementById('settings-notify-downloads')?.checked), quietStart: document.getElementById('settings-quiet-start')?.value || '', quietEnd: document.getElementById('settings-quiet-end')?.value || '' };
    localStorage.setItem(NOTIFICATION_PREFERENCES_KEY, JSON.stringify(values));
    showToast('Bildirim tercihleri kaydedildi.', 'success');
}

async function resetAllPreferences() {
    if (!await showAppConfirm({ title: 'Ayarları Sıfırla', message: 'Okuyucu, ağ, kaynak sırası ve bildirim tercihleri varsayılana dönecek.', confirmText: 'Sıfırla', icon: 'fa-rotate-left' })) return;
    await fetch('/api/preferences/reset', { method: 'POST' });
    [ADVANCED_CLIENT_SETTINGS_KEY, NOTIFICATION_PREFERENCES_KEY, 'downloadCompressionProfile', READER_PREFERENCES_STORAGE_KEY, READER_MODE_STORAGE_KEY].forEach(key => localStorage.removeItem(key));
    location.reload();
}

const downloadCompressionHints = {
    quality: '2400 px, WebP %82. Büyük ekran ve yakınlaştırma için.',
    balanced: '1800 px, WebP %68. Metin netliği ve alan arasında önerilen denge.',
    compact: '1440 px, WebP %60. Telefon ve düşük depolama için en küçük seçenek.'
};

function getDownloadCompressionProfile() {
    if (getAppPreference('low_bandwidth_mode', false)) return 'compact';
    const profile = localStorage.getItem('downloadCompressionProfile') || 'balanced';
    return Object.prototype.hasOwnProperty.call(downloadCompressionHints, profile) ? profile : 'balanced';
}

function loadDownloadCompressionSetting() {
    const profile = getDownloadCompressionProfile();
    const select = document.getElementById('download-compression-profile');
    const hint = document.getElementById('download-compression-hint');
    if (select) select.value = profile;
    if (hint) hint.textContent = downloadCompressionHints[profile];
}

function setDownloadCompressionProfile(profile) {
    if (!Object.prototype.hasOwnProperty.call(downloadCompressionHints, profile)) return;
    localStorage.setItem('downloadCompressionProfile', profile);
    loadDownloadCompressionSetting();
    showToast('Yeni indirmeler için görüntü profili kaydedildi.', 'success');
}


// ═══════════════════════════════════════════════════════════════════════════
// SETTINGS TAB
// ═══════════════════════════════════════════════════════════════════════════

// Analiz sonucu (geçici state)
let _analyzeResult = null;













/**
 * Kaynak listesini yükle ve Eklentiler ekranındaki kaynak alanını doldur.
 * Built-in ve custom kaynakları toggle/sil butonlarıyla gösterir.
 */


/** Seçilen kaynağı gerçek bir arama isteğiyle anlık test et. */




/** Bütün kaynak testlerini aynı anda başlat ve tamamlanma ilerlemesini göster. */


/** Son testlerde sorunlu bulunan kaynakları paylaşılabilir TXT dosyasına kaydet. */


/** Kaynağı aktif/pasif yap */


/** Custom kaynağı sil */


/**
 * Site analizi başlat — 4 yöntem denenir (httpx → cloudscraper → curl_cffi → Selenium)
 */


/** Siteyi analiz sonucuyla veya analiz yapmadan doğrudan ekle. */


// ─── Yardımcı fonksiyonlar ─────────────────────────────────────────────────









