let malIntegrationState = null;
let malImportEntries = [];
let malConnectionPollTimer = null;

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
    const bar = document.getElementById('mal-connection-bar');
    const state = bar?.querySelector('.mal-account-state');
    if (state) {
        const icon = status.connected ? 'fa-circle-check' : (status.configured ? 'fa-link' : 'fa-gear');
        const title = status.connected ? `${status.username || 'MyAnimeList'} bağlı` : (status.configured ? 'Bağlanmaya hazır' : 'Yapılandırma gerekli');
        const detail = !status.secure_storage
            ? (status.storage_error || 'Windows güvenli depolama kullanılamıyor.')
            : status.connected
                ? 'Liste salt okunur olarak içe aktarılabilir.'
                : status.configured
                    ? 'Hesabı Bağla ile MAL izin ekranını açın.'
                    : 'Client ID ve kayıtlı dönüş adresini girin.';
        state.className = `mal-account-state ${status.connected ? 'connected' : ''}`;
        state.innerHTML = `<i class="fa-solid ${icon}"></i><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
    }
    document.getElementById('mal-connect-btn')?.classList.toggle('hidden', status.connected);
    document.getElementById('mal-disconnect-btn')?.classList.toggle('hidden', !status.connected);
    document.getElementById('mal-import-panel')?.classList.toggle('hidden', !status.connected);
}

async function loadMalIntegrationStatus() {
    try {
        renderMalIntegrationStatus(await malApi('/api/integrations/mal/status'));
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveMalConfiguration({ silent = false } = {}) {
    const clientId = document.getElementById('mal-client-id')?.value.trim() || '';
    const clientSecret = document.getElementById('mal-client-secret')?.value || '';
    if (!clientId) {
        if (!silent) showToast('MyAnimeList Client ID girilmelidir.', 'error');
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
                    if (status.connected) showToast(`${status.username || 'MyAnimeList'} hesabı bağlandı.`, 'success');
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
        showToast('MyAnimeList bağlantısı kesildi.', 'success');
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
