let malIntegrationState = null;
let malImportEntries = [];
let malConnectionPollTimer = null;
let malSyncPollTimer = null;
let malLastHandledJobId = '';

const malActiveSyncStates = new Set(['pending', 'fetching', 'matching', 'importing']);
const malSyncStateLabels = {
    idle: 'Henüz çalışmadı',
    pending: 'Senkronizasyon bekliyor',
    fetching: 'MAL listesi alınıyor',
    matching: 'Manga kayıtları eşleştiriliyor',
    importing: 'Kütüphane güncelleniyor',
    completed: 'Senkronizasyon tamamlandı',
    failed: 'Senkronizasyon tamamlanamadı',
    cancelled: 'Senkronizasyon iptal edildi',
};

const malSyncStageLabels = {
    idle: 'HAZIR',
    pending: 'BEKLİYOR',
    fetching: 'LİSTE ALINIYOR',
    matching: 'EŞLEŞTİRİLİYOR',
    importing: 'KÜTÜPHANEYE İŞLENİYOR',
    completed: 'TAMAMLANDI',
    failed: 'HATA',
    cancelled: 'İPTAL EDİLDİ',
};

const malStatusLabels = {
    reading: 'Okunuyor',
    completed: 'Tamamlandı',
    on_hold: 'Beklemede',
    dropped: 'Bırakıldı',
    plan_to_read: 'Okuma planı',
};

async function malApi(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'MyAnimeList işlemi tamamlanamadı.');
    return data;
}

function renderMalIntegrationStatus(status) {
    malIntegrationState = status;
    const clientId = document.getElementById('mal-client-id');
    const callback = document.getElementById('mal-callback-url');
    if (clientId && !clientId.value) clientId.value = status.client_id || '';
    if (callback) callback.value = status.callback_url || '';
    document.getElementById('mal-disconnected-view')?.classList.toggle('hidden', status.connected);
    document.getElementById('mal-connected-view')?.classList.toggle('hidden', !status.connected);
    document.getElementById('mal-import-panel')?.classList.toggle('hidden', !status.connected);
    const username = document.getElementById('mal-account-username');
    if (username) username.textContent = status.username || 'MyAnimeList';
    const automaticSync = document.getElementById('mal-auto-sync-toggle');
    if (automaticSync) automaticSync.checked = status.automatic_sync !== false;
    const twoWaySync = document.getElementById('mal-two-way-sync-toggle');
    if (twoWaySync) twoWaySync.checked = status.two_way_sync === true;
    const syncInterval = document.getElementById('mal-sync-interval');
    if (syncInterval) syncInterval.value = status.sync_interval || '24h';
    const lastSync = status.last_success && typeof status.last_success === 'object' ? status.last_success : {};
    const lastError = status.last_error && typeof status.last_error === 'object' ? status.last_error : {};
    const lastSyncTime = document.getElementById('mal-last-sync-time');
    const totalCount = document.getElementById('mal-total-count');
    const lastErrorNode = document.getElementById('mal-last-sync-error');
    if (lastSyncTime) lastSyncTime.textContent = formatMalSyncTime(lastSync.completed_at);
    if (totalCount) totalCount.textContent = Number(lastSync.total) >= 0 && lastSync.completed_at ? Number(lastSync.total) : '—';
    if (lastErrorNode) {
        const errorText = String(lastError.error || 'Yok');
        lastErrorNode.textContent = errorText;
        lastErrorNode.title = errorText === 'Yok' ? '' : `${formatMalSyncTime(lastError.completed_at)} · ${errorText}`;
    }
    if (!status.secure_storage && status.storage_error) {
        showToast(status.storage_error, 'error');
    }
}

async function loadMalIntegrationStatus() {
    try {
        const status = await malApi('/api/integrations/mal/status');
        renderMalIntegrationStatus(status);
        if (status.connected) {
            await loadMalSyncState();
            await loadMalOutboundStatus();
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveMalConfiguration({ silent = false } = {}) {
    const clientId = document.getElementById('mal-client-id')?.value.trim() || '';
    const clientSecret = document.getElementById('mal-client-secret')?.value || '';
    if (!clientId) {
        if (!silent) showToast('MyAnimeList Client ID girilmelidir.', 'error');
        document.getElementById('mal-api-settings')?.setAttribute('open', '');
        return false;
    }
    try {
        const status = await malApi('/api/integrations/mal/configure', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
        });
        const secretInput = document.getElementById('mal-client-secret');
        if (secretInput) secretInput.value = '';
        renderMalIntegrationStatus(status);
        if (!silent) showToast('MyAnimeList yapılandırması kaydedildi.', 'success');
        return true;
    } catch (error) {
        showToast(error.message, 'error');
        return false;
    }
}

async function connectMyAnimeList() {
    if (!await saveMalConfiguration({ silent: true })) return;
    const button = document.getElementById('mal-connect-btn');
    if (button) button.disabled = true;
    try {
        await malApi('/api/integrations/mal/connect', { method: 'POST' });
        showToast('MyAnimeList izin ekranı tarayıcıda açıldı.', 'info');
        clearInterval(malConnectionPollTimer);
        let attempts = 0;
        malConnectionPollTimer = setInterval(async () => {
            attempts += 1;
            try {
                const status = await malApi('/api/integrations/mal/status');
                renderMalIntegrationStatus(status);
                if (status.connected || attempts >= 120) {
                    clearInterval(malConnectionPollTimer);
                    if (status.connected) {
                        showToast(
                            status.automatic_sync === false
                                ? `${status.username || 'MyAnimeList'} hesabı bağlandı.`
                                : `${status.username || 'MyAnimeList'} hesabı bağlandı. Kütüphane eşitleniyor.`,
                            'success',
                        );
                        await loadMalSyncState();
                    }
                }
            } catch (_) { /* izin penceresi açıkken sessizce bekle */ }
        }, 1000);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function disconnectMyAnimeList() {
    const confirmed = await showAppConfirm({
        title: 'MyAnimeList Bağlantısını Kes',
        message: 'Şifreli erişim anahtarı bu bilgisayardan silinecek. İçe aktarılan mangalar kütüphanede kalır.',
        confirmText: 'Bağlantıyı Kes',
        variant: 'danger',
        icon: 'fa-link-slash',
    });
    if (!confirmed) return;
    try {
        renderMalIntegrationStatus(await malApi('/api/integrations/mal/disconnect', { method: 'DELETE' }));
        malImportEntries = [];
        renderMalImportEntries();
        clearInterval(malSyncPollTimer);
        malSyncPollTimer = null;
        renderMalSyncState({ status: 'idle', message: 'Hesap bağlandığında ilk eşitleme otomatik başlar.' });
        showToast('MyAnimeList bağlantısı kesildi.', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function updateMalAutomaticSync(enabled) {
    const toggle = document.getElementById('mal-auto-sync-toggle');
    if (toggle) toggle.disabled = true;
    try {
        const status = await malApi('/api/integrations/mal/sync/preferences', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                automatic_sync: Boolean(enabled),
                sync_interval: document.getElementById('mal-sync-interval')?.value || '24h',
                two_way_sync: document.getElementById('mal-two-way-sync-toggle')?.checked === true,
            }),
        });
        renderMalIntegrationStatus(status);
        showToast(
            status.automatic_sync ? 'Otomatik MyAnimeList eşitlemesi açıldı.' : 'Otomatik MyAnimeList eşitlemesi kapatıldı.',
            'success',
        );
    } catch (error) {
        if (toggle) toggle.checked = !enabled;
        showToast(error.message, 'error');
    } finally {
        if (toggle) toggle.disabled = false;
    }
}

async function updateMalSyncInterval(interval) {
    const select = document.getElementById('mal-sync-interval');
    if (select) select.disabled = true;
    try {
        const status = await malApi('/api/integrations/mal/sync/preferences', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                automatic_sync: document.getElementById('mal-auto-sync-toggle')?.checked !== false,
                sync_interval: interval || '24h',
                two_way_sync: document.getElementById('mal-two-way-sync-toggle')?.checked === true,
            }),
        });
        renderMalIntegrationStatus(status);
        showToast('MyAnimeList eşitleme aralığı güncellendi.', 'success');
    } catch (error) {
        if (select) select.value = malIntegrationState?.sync_interval || '24h';
        showToast(error.message, 'error');
    } finally {
        if (select) select.disabled = false;
    }
}

function formatMalSyncTime(value) {
    const timestamp = Number(value) || 0;
    if (!timestamp) return 'Henüz yapılmadı';
    return new Intl.DateTimeFormat('tr-TR', {
        day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(new Date(timestamp * 1000));
}

function renderMalSyncState(job = {}) {
    const state = String(job.status || 'idle');
    const panel = document.getElementById('mal-sync-panel');
    if (!panel) return;
    panel.dataset.state = state;
    const processed = Math.max(0, Number(job.processed) || 0);
    const total = Math.max(0, Number(job.total) || 0);
    const percent = state === 'completed' ? 100 : (total ? Math.min(99, Math.round((processed / total) * 100)) : 0);
    const setText = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = String(value); };
    setText('mal-sync-title', malSyncStateLabels[state] || 'MyAnimeList senkronizasyonu');
    setText('mal-sync-stage', malSyncStageLabels[state] || 'EŞİTLENİYOR');
    setText('mal-sync-message', job.error || job.message || 'Senkronizasyon durumu bekleniyor.');
    setText('mal-sync-processed', `${processed} / ${total || '—'}`);
    setText('mal-sync-added', Number(job.added) || 0);
    setText('mal-sync-updated', Number(job.updated) || 0);
    setText('mal-sync-unchanged', Number(job.unchanged) || 0);
    setText('mal-sync-unmatched', Number(job.unmatched) || 0);
    setText('mal-sync-failed', Number(job.failed) || 0);
    if (job.status === 'completed' && job.completed_at) {
        setText('mal-last-sync-time', formatMalSyncTime(job.completed_at));
        setText('mal-total-count', total);
        setText('mal-last-sync-error', 'Yok');
    } else if (job.status === 'failed' && job.completed_at) {
        setText('mal-last-sync-error', job.error || 'MyAnimeList eşitlemesi tamamlanamadı.');
    }
    const bar = document.getElementById('mal-sync-progress-bar');
    if (bar) bar.style.width = `${percent}%`;
    const progress = document.getElementById('mal-sync-progress');
    if (progress) {
        progress.setAttribute('aria-valuenow', String(percent));
        progress.setAttribute('aria-valuetext', total ? `${processed} / ${total} kayıt` : malSyncStateLabels[state]);
    }
    const active = malActiveSyncStates.has(state);
    document.getElementById('mal-sync-start-btn')?.classList.toggle('hidden', active);
    document.getElementById('mal-sync-cancel-btn')?.classList.toggle('hidden', !active);
}

async function handleMalSyncState(job) {
    renderMalSyncState(job);
    if (malActiveSyncStates.has(job.status)) {
        if (!malSyncPollTimer) {
            malSyncPollTimer = setInterval(() => loadMalSyncState({ quiet: true }), 800);
        }
        return;
    }
    clearInterval(malSyncPollTimer);
    malSyncPollTimer = null;
    if (job.status === 'completed' && job.job_id && job.job_id !== malLastHandledJobId) {
        malLastHandledJobId = job.job_id;
        await loadLibrary();
        await loadMalOutboundStatus();
        showToast(`MAL eşitlendi: ${Number(job.added) || 0} eklendi, ${Number(job.updated) || 0} güncellendi.`, 'success');
    } else if (job.status === 'failed' && job.job_id && job.job_id !== malLastHandledJobId) {
        malLastHandledJobId = job.job_id;
        showToast(job.error || 'MyAnimeList senkronizasyonu tamamlanamadı.', 'error');
    }
}

async function loadMalSyncState({ quiet = false } = {}) {
    try {
        let job = await malApi('/api/integrations/mal/sync/status');
        if (job.status === 'idle') {
            const summary = await malApi('/api/integrations/mal/sync/summary');
            if (summary.status !== 'idle') job = summary;
        }
        await handleMalSyncState(job);
    } catch (error) {
        if (!quiet) showToast(error.message, 'error');
    }
}

async function startMalSync() {
    const button = document.getElementById('mal-sync-start-btn');
    if (button) button.disabled = true;
    try {
        await handleMalSyncState(await malApi('/api/integrations/mal/sync', { method: 'POST' }));
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function cancelMalSync() {
    try {
        await handleMalSyncState(await malApi('/api/integrations/mal/sync', { method: 'DELETE' }));
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function loadMalImportPreview(force = false) {
    const button = document.getElementById('mal-preview-btn');
    const list = document.getElementById('mal-import-list');
    if (button) button.disabled = true;
    if (list) list.innerHTML = '<div class="settings-inline-empty"><i class="fa-solid fa-spinner fa-spin"></i> MyAnimeList okuma listesi hazırlanıyor…</div>';
    try {
        const data = await malApi(`/api/integrations/mal/preview?force=${force ? 'true' : 'false'}`);
        malImportEntries = Array.isArray(data.entries) ? data.entries : [];
        const summary = document.getElementById('mal-import-summary');
        if (summary) {
            summary.classList.remove('hidden');
            summary.innerHTML = `<span><strong>${data.total || 0}</strong> MAL kaydı</span><span class="matched"><strong>${data.matched || 0}</strong> kesin eşleşme</span><span class="unmatched"><strong>${data.unmatched || 0}</strong> eşleşmedi</span>`;
        }
        document.getElementById('mal-import-toolbar')?.classList.remove('hidden');
        renderMalImportEntries();
    } catch (error) {
        if (list) list.innerHTML = `<div class="settings-inline-empty">${escapeHtml(error.message)}</div>`;
        showToast(error.message, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

function renderMalImportEntries() {
    const list = document.getElementById('mal-import-list');
    if (!list) return;
    if (!malImportEntries.length) {
        list.innerHTML = '<div class="settings-inline-empty">Liste henüz yüklenmedi.</div>';
        return;
    }
    list.innerHTML = malImportEntries.map(entry => {
        const matched = Boolean(entry.matched && entry.manga);
        const cover = entry.manga?.cover_url || entry.cover_url || '/static/img/no-cover.jpg';
        const progress = Number(entry.num_chapters_read) > 0 ? ` · ${Number(entry.num_chapters_read)} bölüm` : '';
        return `<label class="mal-import-row ${matched ? 'matched' : 'unmatched'}">
            <input class="mal-entry-checkbox" type="checkbox" value="${Number(entry.mal_id)}" ${matched ? 'checked' : 'disabled'}>
            <img src="${escapeHtml(cover)}" alt="" loading="lazy" decoding="async">
            <span><strong>${escapeHtml(entry.manga?.title || entry.title)}</strong><small>${escapeHtml(malStatusLabels[entry.status] || entry.status)}${progress}${Number(entry.score) ? ` · ${Number(entry.score)}/10` : ''}</small></span>
            <em>${matched ? '<i class="fa-solid fa-link"></i> Manga kaydı hazır' : '<i class="fa-solid fa-triangle-exclamation"></i> Eşleşmedi'}</em>
        </label>`;
    }).join('');
}

function toggleAllMalEntries(checked) {
    document.querySelectorAll('.mal-entry-checkbox:not(:disabled)').forEach(input => { input.checked = Boolean(checked); });
}

async function importSelectedMalEntries() {
    const malIds = [...document.querySelectorAll('.mal-entry-checkbox:checked')].map(input => Number(input.value)).filter(Boolean);
    if (!malIds.length) return showToast('İçe aktarılacak en az bir manga seçin.', 'error');
    const button = document.getElementById('mal-import-btn');
    if (button) button.disabled = true;
    try {
        const result = await malApi('/api/integrations/mal/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mal_ids: malIds }),
        });
        await loadLibrary();
        showToast(result.message || `${result.imported || 0} manga içe aktarıldı.`, result.failed?.length ? 'info' : 'success');
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function updateMalTwoWaySync(enabled) {
    const toggle = document.getElementById('mal-two-way-sync-toggle');
    if (toggle) toggle.disabled = true;
    if (enabled) {
        const confirmed = await showAppConfirm({
            title: 'Çift Yönlü MAL Eşitlemesi',
            message: 'MangaX’te değiştirdiğin okuma durumu, puan, bölüm ve cilt ilerlemesi MyAnimeList hesabına gönderilecek. Notlar, koleksiyonlar ve dosyalar gönderilmez.',
            confirmText: 'Etkinleştir',
            variant: 'primary',
            icon: 'fa-arrows-rotate',
        });
        if (!confirmed) {
            if (toggle) {
                toggle.checked = false;
                toggle.disabled = false;
            }
            return;
        }
    }
    try {
        const status = await malApi('/api/integrations/mal/sync/preferences', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                automatic_sync: document.getElementById('mal-auto-sync-toggle')?.checked !== false,
                sync_interval: document.getElementById('mal-sync-interval')?.value || '24h',
                two_way_sync: Boolean(enabled),
            }),
        });
        renderMalIntegrationStatus(status);
        await loadMalOutboundStatus();
        showToast(
            enabled ? 'Çift yönlü MAL eşitlemesi etkinleştirildi.' : 'Çift yönlü MAL eşitlemesi kapatıldı.',
            'success',
        );
    } catch (error) {
        if (toggle) toggle.checked = !enabled;
        showToast(error.message, 'error');
    } finally {
        if (toggle) toggle.disabled = false;
    }
}

function renderMalOutboundStatus(status = {}) {
    const summary = document.getElementById('mal-outbound-summary');
    const list = document.getElementById('mal-conflict-list');
    const retry = document.getElementById('mal-outbound-retry');
    if (!summary || !list) return;
    const pending = Number(status.pending) || 0;
    const conflicts = Number(status.conflicts) || 0;
    const failed = Number(status.failed) || 0;
    summary.textContent = status.enabled
        ? `${pending} bekliyor · ${conflicts} çakışma · ${failed} yeniden denenecek`
        : 'Çift yönlü eşitleme kapalı.';
    retry?.classList.toggle('hidden', !status.enabled || (!pending && !failed));
    const conflictItems = (Array.isArray(status.items) ? status.items : [])
        .filter(item => item.state === 'conflict');
    list.innerHTML = conflictItems.map(item => `
        <article class="mal-conflict-row">
            <div><strong>${escapeHtml(item.title || 'Manga')}</strong><small>MAL ve MangaX’te aynı kayıt değişmiş. Hangi sürüm korunsun?</small></div>
            <div>
                <button class="btn btn-secondary" type="button" onclick="resolveMalConflict('${escapeHtml(item.manga_id)}', 'remote')">MAL’daki sürümü kullan</button>
                <button class="btn btn-primary" type="button" onclick="resolveMalConflict('${escapeHtml(item.manga_id)}', 'local')">MangaX sürümünü kullan</button>
            </div>
        </article>
    `).join('');
}

async function loadMalOutboundStatus() {
    try {
        renderMalOutboundStatus(await malApi('/api/integrations/mal/outbound/status'));
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function retryMalOutbound() {
    try {
        const status = await malApi('/api/integrations/mal/outbound/retry', { method: 'POST' });
        renderMalOutboundStatus(status);
        showToast('Bekleyen MAL değişiklikleri yeniden sıraya alındı.', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function resolveMalConflict(mangaId, choice) {
    try {
        const status = await malApi(`/api/integrations/mal/outbound/conflicts/${encodeURIComponent(mangaId)}/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ choice }),
        });
        renderMalOutboundStatus(status);
        if (choice === 'remote') await loadLibrary();
        showToast(
            choice === 'remote' ? 'MyAnimeList sürümü uygulandı.' : 'MangaX sürümü gönderim sırasına alındı.',
            'success',
        );
    } catch (error) {
        showToast(error.message, 'error');
    }
}
