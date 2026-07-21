const BACKUP_CLIENT_SETTING_KEYS = Object.freeze([
    'show18Plus',
    'showMangaNews',
    'downloadCompressionProfile',
    'mangax-preferred-language-v1',
    'mangax-reader-mode',
    'mangax-reader-preferences-v1',
    'mangax-onboarding-completed-v1',
    'mangax-onboarding-completed-v2',
    'mangax-reader-onboarding-completed-v1',
    'mangax-advanced-settings-v1',
    'mangax-notification-preferences-v1',
    'mangax-sidebar-collapsed-v1',
]);
let lastSyncedLocalBackupClientSettings = '';
let localBackupClientSyncTimer = null;

function collectBackupClientSettings() {
    const settings = {};
    BACKUP_CLIENT_SETTING_KEYS.forEach(key => {
        try {
            const value = localStorage.getItem(key);
            if (value !== null) settings[key] = value;
        } catch (_) { /* erişilemeyen ayar atlanır */ }
    });
    return settings;
}

function applyBackupClientSettings(settings) {
    if (!settings || typeof settings !== 'object') return;
    BACKUP_CLIENT_SETTING_KEYS.forEach(key => {
        if (!Object.prototype.hasOwnProperty.call(settings, key)) return;
        try { localStorage.setItem(key, String(settings[key])); } catch (_) { /* atla */ }
    });
    if (typeof setPreferredAppLanguage === 'function') {
        setPreferredAppLanguage(settings['mangax-preferred-language-v1'] || 'tr', { render: false });
    }
    if (typeof loadDownloadCompressionSetting === 'function') loadDownloadCompressionSetting();
    if (typeof syncMangaNewsSetting === 'function') syncMangaNewsSetting();
}

async function readBackupResponse(response) {
    let data = {};
    try { data = await response.json(); } catch (_) { /* boş/geçersiz cevap */ }
    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    return data;
}

async function createPortableBackup() {
    const response = await fetch('/api/backup/export', { cache: 'no-store' });
    const backup = await readBackupResponse(response);
    backup.client_settings = collectBackupClientSettings();
    return backup;
}

function backupFilename() {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    return `mangax-yedek-${stamp}.json`;
}

async function exportMangaXBackup() {
    const button = document.getElementById('backup-export-btn');
    if (button) button.disabled = true;
    try {
        const backup = await createPortableBackup();
        const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = backupFilename();
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        showToast(`${backup.library.length} manga ve ${backup.reading_history_count} okuma kaydı dışa aktarıldı.`, 'success');
    } catch (error) {
        showToast(`Yedek oluşturulamadı: ${error.message}`, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

function chooseMangaXBackupFile() {
    document.getElementById('backup-import-file')?.click();
}

async function handleMangaXBackupFile(input) {
    const file = input?.files?.[0];
    if (!file) return;
    try {
        const backup = JSON.parse(await file.text());
        await restoreMangaXBackup(backup, `“${file.name}” yedeği mevcut kütüphaneyle birleştirilecek.`);
    } catch (error) {
        showToast(`Yedek okunamadı: ${error.message}`, 'error');
    } finally {
        input.value = '';
    }
}

async function restoreMangaXBackup(backup, confirmationMessage) {
    const localPageNote = typeof isGithubExtensionsAvailable === 'function' && isGithubExtensionsAvailable()
        ? ' Cihazdaki indirilmiş sayfa dosyaları değiştirilmez.'
        : '';
    const accepted = await showAppConfirm({
        title: 'MangaX Yedeğini Geri Yükle',
        message: `${confirmationMessage}${localPageNote}`,
        confirmText: 'Birleştir ve Geri Yükle',
        icon: 'fa-box-open',
    });
    if (!accepted) return false;

    showToast('Kütüphane, geçmiş ve kaynak ayarları geri yükleniyor…', 'info');
    const response = await fetch('/api/backup/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backup }),
    });
    const result = await readBackupResponse(response);
    applyBackupClientSettings(result.client_settings || backup.client_settings || {});
    await loadLibrary();
    if (typeof loadExtensionsTab === 'function') await loadExtensionsTab();
    await loadBackupOverview();
    showToast(`${result.mangas_imported} manga ve ${result.history_imported} okuma kaydı geri yüklendi.`, 'success');
    return true;
}

async function loadBackupOverview() {
    const summary = document.getElementById('backup-library-summary');
    if (!summary) return;
    try {
        const backup = await createPortableBackup();
        summary.textContent = `${backup.library.length} manga · ${backup.reading_history_count} okuma geçmişi kaydı`;
    } catch (_) {
        summary.textContent = 'Yedek özeti alınamadı';
    }
    await loadLocalBackupOverview();
}

function localBackupReasonLabel(reason) {
    return ({
        scheduled: 'Zamanlanmış yedek',
        shutdown: 'Kapanış yedeği',
        manual: 'Manuel yedek',
        'before-restore': 'Geri yükleme öncesi güvenlik yedeği',
    })[reason] || 'Yerel yedek';
}

function localBackupDate(value) {
    const date = new Date(value || '');
    return Number.isNaN(date.getTime()) ? 'Tarih bilinmiyor' : date.toLocaleString('tr-TR');
}

function localBackupSize(bytes) {
    if (typeof formatStorageBytes === 'function') return formatStorageBytes(bytes || 0);
    return `${Math.max(1, Math.round((Number(bytes) || 0) / 1024))} KB`;
}

function renderLocalBackups(backups) {
    const list = document.getElementById('local-backup-list');
    if (!list) return;
    const { clear, element, icon, text } = window.MangaXSafeDOM;
    clear(list);
    if (!Array.isArray(backups) || backups.length === 0) {
        list.appendChild(element('div', { className: 'local-backup-empty' }, [icon('fa-solid fa-box-open'), text(' Henüz yerel yedek oluşturulmadı.')]));
        return;
    }
    backups.forEach(backup => {
        const restore = element('button', { className: 'btn btn-secondary local-backup-restore', type: 'button' }, [icon('fa-solid fa-rotate-left'), text(' Geri Dön')]);
        restore.addEventListener('click', () => restoreLocalBackup(String(backup.id ?? '')));
        list.appendChild(element('article', { className: 'local-backup-item' }, [
            icon('fa-solid fa-clock-rotate-left'),
            element('div', { className: 'local-backup-item-copy' }, [
                element('strong', { text: localBackupDate(backup.created_at) }),
                element('span', { text: `${localBackupReasonLabel(backup.reason)} · ${Number(backup.manga_count) || 0} manga · ${localBackupSize(backup.size_bytes)}` }),
            ]),
            restore,
        ]));
    });
}

function syncLocalBackupControls() {
    const enabled = Boolean(document.getElementById('local-backup-enabled')?.checked);
    const controls = document.getElementById('local-backup-controls');
    controls?.classList.toggle('disabled', !enabled);
    const interval = document.getElementById('local-backup-interval');
    const retention = document.getElementById('local-backup-retention');
    if (interval) interval.disabled = !enabled;
    if (retention) retention.disabled = !enabled;
    const status = document.getElementById('local-backup-status');
    if (status && !enabled) status.textContent = 'Otomatik ve kapanış yedekleri kapalı. Manuel yedek oluşturabilirsin.';
}

function getLocalBackupSettingsPayload() {
    return {
        enabled: Boolean(document.getElementById('local-backup-enabled')?.checked),
        interval_minutes: Number(document.getElementById('local-backup-interval')?.value) || 30,
        retention_count: Number(document.getElementById('local-backup-retention')?.value) || 5,
        client_settings: collectBackupClientSettings(),
    };
}

async function persistLocalBackupSettings({ notify = false, refresh = false } = {}) {
    const payload = getLocalBackupSettingsPayload();
    const response = await fetch('/api/backup/local/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        keepalive: true,
    });
    await readBackupResponse(response);
    lastSyncedLocalBackupClientSettings = JSON.stringify(payload.client_settings);
    if (notify) showToast(payload.enabled ? 'Otomatik yerel yedekleme güncellendi.' : 'Otomatik yerel yedekleme kapatıldı.', 'success');
    if (refresh) await loadLocalBackupOverview({ syncClientSettings: false });
}

async function updateLocalBackupSettings() {
    syncLocalBackupControls();
    try {
        await persistLocalBackupSettings({ notify: true, refresh: true });
    } catch (error) {
        showToast(`Yedekleme ayarı kaydedilemedi: ${error.message}`, 'error');
    }
}

async function loadLocalBackupOverview({ syncClientSettings = true } = {}) {
    const status = document.getElementById('local-backup-status');
    if (!status) return;
    try {
        const response = await fetch('/api/backup/local', { cache: 'no-store' });
        const data = await readBackupResponse(response);
        const settings = data.settings || {};
        document.getElementById('local-backup-enabled').checked = settings.enabled !== false;
        document.getElementById('local-backup-interval').value = String(settings.interval_minutes || 30);
        document.getElementById('local-backup-retention').value = String(settings.retention_count || 5);
        renderLocalBackups(data.backups || []);
        syncLocalBackupControls();
        if (settings.enabled !== false) {
            const latest = data.backups?.[0];
            status.textContent = latest
                ? `Son yedek: ${localBackupDate(latest.created_at)} · ${localBackupReasonLabel(latest.reason)}`
                : `Her ${settings.interval_minutes || 30} dakikada bir yedek alınacak.`;
        }
        lastSyncedLocalBackupClientSettings = JSON.stringify(settings.client_settings || {});
        if (syncClientSettings) await syncLocalBackupClientSettings();
    } catch (error) {
        status.textContent = `Yerel yedekler yüklenemedi: ${error.message}`;
        const list = document.getElementById('local-backup-list');
        if (list) list.innerHTML = '<div class="local-backup-empty">Yerel yedek bilgisi alınamadı.</div>';
    }
}

async function syncLocalBackupClientSettings() {
    if (!document.getElementById('local-backup-enabled')) return;
    const current = JSON.stringify(collectBackupClientSettings());
    if (current === lastSyncedLocalBackupClientSettings) return;
    try {
        await persistLocalBackupSettings();
    } catch (_) { /* sonraki periyodik kontrolde yeniden denenir */ }
}

async function createLocalBackupNow() {
    const button = document.getElementById('local-backup-now');
    if (button) button.disabled = true;
    try {
        const response = await fetch('/api/backup/local/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_settings: collectBackupClientSettings() }),
        });
        const data = await readBackupResponse(response);
        showToast(data.message || 'Yerel yedek oluşturuldu.', 'success');
        await loadLocalBackupOverview({ syncClientSettings: false });
    } catch (error) {
        showToast(`Yerel yedek oluşturulamadı: ${error.message}`, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function restoreLocalBackup(backupId) {
    const localPageNote = typeof isGithubExtensionsAvailable === 'function' && isGithubExtensionsAvailable()
        ? ' İndirilen manga görselleri silinmez.'
        : '';
    const accepted = await showAppConfirm({
        title: 'Önceki Yedeğe Dön',
        message: `Mevcut durum için önce bir güvenlik yedeği alınacak, ardından seçilen yedek kütüphaneyle birleştirilecek.${localPageNote}`,
        confirmText: 'Güvenle Geri Dön',
        icon: 'fa-clock-rotate-left',
    });
    if (!accepted) return;
    try {
        showToast('Güvenlik yedeği alınıyor ve önceki durum yükleniyor…', 'info');
        const response = await fetch(`/api/backup/local/${encodeURIComponent(backupId)}/restore`, {
            method: 'POST',
        });
        const result = await readBackupResponse(response);
        applyBackupClientSettings(result.client_settings || {});
        await loadLibrary();
        if (typeof loadExtensionsTab === 'function') await loadExtensionsTab();
        await loadBackupOverview();
        showToast(`${result.mangas_imported} manga ve ${result.history_imported} okuma kaydı geri yüklendi.`, 'success');
    } catch (error) {
        showToast(`Yerel yedek geri yüklenemedi: ${error.message}`, 'error');
    }
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        loadBackupOverview();
        localBackupClientSyncTimer = setInterval(syncLocalBackupClientSettings, 30000);
    });
}
