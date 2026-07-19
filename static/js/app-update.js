let appUpdateJobId = '';
let appUpdatePollTimer = null;
let appUpdateStartupChecked = false;

function scheduleStartupUpdateCheck(attempt = 0) {
    const blockingOverlay = document.querySelector('#onboarding-overlay.active, #reader-onboarding-overlay.active');
    if (blockingOverlay && attempt < 120) {
        setTimeout(() => scheduleStartupUpdateCheck(attempt + 1), 1000);
        return;
    }
    checkForAppUpdate({ startup: true });
}

function formatUpdateBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function renderAppUpdateStatus(message, progress = null, state = '') {
    const status = document.getElementById('app-update-status');
    const progressWrap = document.getElementById('app-update-progress');
    const progressBar = document.getElementById('app-update-progress-bar');
    const cancel = document.getElementById('app-update-cancel-btn');
    if (status) {
        status.textContent = message || '';
        status.dataset.state = state;
    }
    const checking = state === 'checking';
    if (progressWrap) {
        progressWrap.classList.toggle('hidden', progress === null && !checking);
        progressWrap.classList.toggle('is-indeterminate', checking);
        progressWrap.setAttribute('aria-hidden', String(progress === null && !checking));
    }
    if (progressBar && progress !== null && !checking) {
        progressBar.style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;
    }
    if (cancel) cancel.classList.toggle('hidden', !['downloading', 'ready'].includes(state));
}

async function readUpdateError(response, fallback) {
    try {
        const payload = await response.json();
        return payload.detail || fallback;
    } catch (_) {
        return fallback;
    }
}

async function checkForAppUpdate({ manual = false, startup = false } = {}) {
    if (startup && appUpdateStartupChecked) return;
    if (startup) appUpdateStartupChecked = true;
    const button = document.getElementById('app-update-check-btn');
    if (button) button.disabled = true;
    const buttonIcon = button?.querySelector('i');
    buttonIcon?.classList.add('fa-spin');
    renderAppUpdateStatus('Yeni sürüm kontrol ediliyor…', null, 'checking');
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch(`/api/updates/check?startup=${startup ? 'true' : 'false'}`, {
            cache: 'no-store',
            signal: controller.signal,
        });
        if (!response.ok) throw new Error(await readUpdateError(response, 'Güncelleme kontrol edilemedi.'));
        const update = await response.json();
        if (update.skipped) {
            renderAppUpdateStatus('Açılışta otomatik güncelleme kontrolü kapalı.', null, 'skipped');
            return;
        }
        if (!update.update_available) {
            renderAppUpdateStatus(`MangaX ${update.current_version} güncel.`, null, 'current');
            if (manual && typeof showToast === 'function') showToast('MangaX güncel.', 'success');
            return;
        }
        renderAppUpdateStatus(`${update.latest_version} sürümü hazır · ${formatUpdateBytes(update.size)}`, null, 'available');
        const accepted = await showAppConfirm({
            title: 'MangaX Güncellemesi Hazır',
            message: `${update.channel} ${update.latest_version} sürümü (${formatUpdateBytes(update.size)}) indirilecek, doğrulanacak ve kurulacak. Uygulama kurulum için kapanacaktır.`,
            confirmText: 'İndir ve Kur',
            cancelText: 'Daha Sonra',
            icon: 'fa-cloud-arrow-down'
        });
        if (!accepted) return;
        await startAppUpdateDownload(update.update_id);
    } catch (error) {
        const message = error?.name === 'AbortError'
            ? 'Güncelleme kontrolü zaman aşımına uğradı. Daha sonra yeniden deneyin.'
            : (error.message || 'Güncelleme kontrol edilemedi.');
        renderAppUpdateStatus(message, null, 'error');
        if (manual) {
            if (typeof showToast === 'function') showToast(message, 'error');
        } else {
            console.warn('Otomatik güncelleme kontrolü atlandı:', error);
        }
    } finally {
        clearTimeout(timeoutId);
        if (button) button.disabled = false;
        buttonIcon?.classList.remove('fa-spin');
    }
}

async function startAppUpdateDownload(updateId) {
    renderAppUpdateStatus('Güncelleme indirilmeye hazırlanıyor…', 0, 'downloading');
    const response = await fetch('/api/updates/download', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ update_id: updateId, confirmed: true })
    });
    if (!response.ok) throw new Error(await readUpdateError(response, 'Güncelleme indirilemedi.'));
    const job = await response.json();
    appUpdateJobId = job.job_id;
    pollAppUpdateDownload();
}

async function pollAppUpdateDownload() {
    if (!appUpdateJobId) return;
    clearTimeout(appUpdatePollTimer);
    try {
        const response = await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(await readUpdateError(response, 'Güncelleme durumu alınamadı.'));
        const job = await response.json();
        if (job.status === 'downloading') {
            renderAppUpdateStatus(`İndiriliyor… %${Math.round(job.progress)}`, job.progress, 'downloading');
            appUpdatePollTimer = setTimeout(pollAppUpdateDownload, 500);
            return;
        }
        if (job.ready_to_install) {
            renderAppUpdateStatus('Dosya doğrulandı. Kurulum başlatılıyor…', 100, 'ready');
            const installResponse = await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}/install`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true })
            });
            if (!installResponse.ok) throw new Error(await readUpdateError(installResponse, 'Kurulum başlatılamadı.'));
            renderAppUpdateStatus('Kurulum başlatıldı. MangaX kapanıyor…', 100, 'installing');
            return;
        }
        if (['failed', 'cancelled'].includes(job.status)) {
            throw new Error(job.error || 'Güncelleme tamamlanamadı.');
        }
    } catch (error) {
        renderAppUpdateStatus(error.message || 'Güncelleme tamamlanamadı.', null, 'error');
        if (typeof showToast === 'function') showToast(error.message || 'Güncelleme tamamlanamadı.', 'error');
    }
}

async function cancelAppUpdate() {
    if (!appUpdateJobId) return;
    clearTimeout(appUpdatePollTimer);
    try {
        await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}`, { method: 'DELETE' });
    } finally {
        appUpdateJobId = '';
        renderAppUpdateStatus('Güncelleme iptal edildi.', null, 'cancelled');
    }
}
