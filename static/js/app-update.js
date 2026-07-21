let appUpdateJobId = '';
let appUpdatePollTimer = null;
let appUpdateStartupChecked = false;
let appUpdateDescriptor = null;
let appUpdateState = 'idle';
let appUpdateLastFocused = null;
let appUpdateCanResume = false;

function scheduleStartupUpdateCheck(attempt = 0) {
    const blockingOverlay = document.querySelector('#onboarding-overlay.active, #reader-onboarding-overlay.active, #release-notes-overlay.active');
    if (blockingOverlay && attempt < 120) {
        setTimeout(() => scheduleStartupUpdateCheck(attempt + 1), 1000);
        return;
    }
    loadLastAppUpdateResult();
    checkForAppUpdate({ startup: true });
}

function formatUpdateBytes(bytes) {
    const value = Math.max(0, Number(bytes) || 0);
    if (value < 1024) return `${value.toFixed(0)} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatUpdateDuration(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    if (!value) return 'Hesaplanıyor';
    if (value < 60) return `${Math.ceil(value)} sn kaldı`;
    return `${Math.ceil(value / 60)} dk kaldı`;
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
    if (cancel) cancel.classList.toggle('hidden', !['downloading', 'verifying', 'ready_to_install'].includes(state));
}

async function readUpdateError(response, fallback) {
    try {
        const payload = await response.json();
        return payload.detail || fallback;
    } catch (_) {
        return fallback;
    }
}

function setAppUpdateText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
}

function renderUpdateNotes(notes) {
    const list = document.getElementById('app-update-notes');
    const wrap = document.getElementById('app-update-notes-wrap');
    if (!list || !wrap) return;
    list.replaceChildren();
    const safeNotes = Array.isArray(notes) ? notes.filter(Boolean).slice(0, 8) : [];
    safeNotes.forEach(note => {
        const item = document.createElement('li');
        item.textContent = String(note);
        list.appendChild(item);
    });
    wrap.classList.toggle('hidden', safeNotes.length === 0);
}

function setAppUpdateModalState(state, message = '') {
    appUpdateState = state;
    const primary = document.getElementById('app-update-primary');
    const primaryLabel = primary?.querySelector('span');
    const primaryIcon = primary?.querySelector('i');
    const live = document.getElementById('app-update-live');
    const ready = document.getElementById('app-update-ready');
    const error = document.getElementById('app-update-error');
    const skip = document.getElementById('app-update-skip');
    const later = document.getElementById('app-update-later');
    const close = document.getElementById('app-update-close');
    const active = ['downloading', 'verifying', 'restarting'].includes(state);
    live?.classList.toggle('hidden', !['downloading', 'verifying'].includes(state));
    ready?.classList.toggle('hidden', state !== 'ready_to_install');
    error?.classList.toggle('hidden', state !== 'failed');
    if (error && state === 'failed') error.textContent = message || 'Güncelleme tamamlanamadı.';
    if (primary) primary.disabled = active;
    if (skip) skip.classList.toggle('hidden', state !== 'available');
    if (later) later.textContent = state === 'ready_to_install' ? 'Daha Sonra' : 'Kapat';
    if (close) close.disabled = state === 'restarting';
    if (primaryLabel) {
        primaryLabel.textContent = state === 'ready_to_install'
            ? 'Yeniden Başlat ve Güncelle'
            : state === 'failed' && appUpdateCanResume ? 'İndirmeye Devam Et' : 'Şimdi Güncelle';
    }
    if (primaryIcon) {
        primaryIcon.className = state === 'ready_to_install' ? 'fa-solid fa-power-off' : state === 'failed' && appUpdateCanResume ? 'fa-solid fa-play' : 'fa-solid fa-download';
    }
    const hero = document.getElementById('app-update-hero-icon');
    hero?.classList.toggle('success', state === 'ready_to_install');
    const heroIcon = hero?.querySelector('i');
    if (heroIcon) heroIcon.className = state === 'ready_to_install' ? 'fa-solid fa-check' : 'fa-solid fa-cloud-arrow-down';
}

function openAppUpdateModal(update) {
    appUpdateDescriptor = update;
    appUpdateLastFocused = document.activeElement;
    setAppUpdateText('app-update-current-version', update.current_version || '—');
    setAppUpdateText('app-update-latest-version', update.latest_version || '—');
    setAppUpdateText('app-update-file-size', formatUpdateBytes(update.size));
    setAppUpdateText('app-update-dialog-title', `${update.latest_version} sürümü hazır`);
    setAppUpdateText('app-update-dialog-description', 'Güncelleme MangaX içinde güvenli biçimde indirilecek ve doğrulanacak. Kurulum için ayrıca onayınız alınacak.');
    renderUpdateNotes(update.notes);
    setAppUpdateModalState('available');
    const overlay = document.getElementById('app-update-overlay');
    overlay?.classList.add('active');
    overlay?.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => document.getElementById('app-update-primary')?.focus());
}

async function closeAppUpdateModal(force = false) {
    if (!force && ['downloading', 'verifying'].includes(appUpdateState)) {
        const closeAnyway = await showAppConfirm({
            title: 'İndirme arka planda sürsün mü?',
            message: 'Pencereyi kapatırsanız indirme MangaX içinde devam eder. İndirmeyi Ayarlar bölümünden yeniden açabilirsiniz.',
            confirmText: 'Arka Planda Sürdür', cancelText: 'Geri Dön', icon: 'fa-download'
        });
        if (!closeAnyway) return;
    }
    const overlay = document.getElementById('app-update-overlay');
    overlay?.classList.remove('active');
    overlay?.setAttribute('aria-hidden', 'true');
    appUpdateLastFocused?.focus?.();
}

async function checkForAppUpdate({ manual = false, startup = false } = {}) {
    if (startup && appUpdateStartupChecked) return;
    if (startup) appUpdateStartupChecked = true;
    const button = document.getElementById('app-update-check-btn');
    if (button?.disabled) return;
    if (button) button.disabled = true;
    const buttonIcon = button?.querySelector('i');
    buttonIcon?.classList.add('fa-spin');
    renderAppUpdateStatus('Yeni sürüm kontrol ediliyor…', null, 'checking');
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch(`/api/updates/check?startup=${startup ? 'true' : 'false'}`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(await readUpdateError(response, 'Güncelleme kontrol edilemedi.'));
        const update = await response.json();
        if (update.checked_at) setAppUpdateText('app-update-last-check', new Date(update.checked_at).toLocaleString('tr-TR'));
        if (update.skipped) {
            renderAppUpdateStatus('Açılışta otomatik güncelleme kontrolü kapalı.', null, 'skipped');
            return;
        }
        if (update.skipped_version) {
            renderAppUpdateStatus(`${update.latest_version} sürümü daha önce atlandı.`, null, 'skipped');
            return;
        }
        if (!update.update_available) {
            renderAppUpdateStatus(`MangaX ${update.current_version} güncel.`, null, 'current');
            if (manual && typeof showToast === 'function') showToast('MangaX güncel.', 'success');
            return;
        }
        renderAppUpdateStatus(`${update.latest_version} sürümü hazır · ${formatUpdateBytes(update.size)}`, null, 'available');
        openAppUpdateModal(update);
    } catch (error) {
        const message = error?.name === 'AbortError' ? 'Güncelleme kontrolü zaman aşımına uğradı.' : (error.message || 'Güncelleme kontrol edilemedi.');
        renderAppUpdateStatus(message, null, 'error');
        if (manual && typeof showToast === 'function') showToast(message, 'error');
    } finally {
        clearTimeout(timeoutId);
        if (button) button.disabled = false;
        buttonIcon?.classList.remove('fa-spin');
    }
}

async function startAppUpdateDownload(updateId) {
    appUpdateCanResume = false;
    setAppUpdateModalState('downloading');
    renderAppUpdateStatus('Güncelleme indiriliyor…', 0, 'downloading');
    const response = await fetch('/api/updates/download', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ update_id: updateId, confirmed: true })
    });
    if (!response.ok) throw new Error(await readUpdateError(response, 'Güncelleme indirilemedi.'));
    const job = await response.json();
    appUpdateJobId = job.job_id;
    pollAppUpdateDownload();
}

function renderAppUpdateJob(job) {
    const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const progressNode = document.getElementById('app-update-dialog-progress');
    const progressBar = document.getElementById('app-update-dialog-progress-bar');
    progressNode?.setAttribute('aria-valuenow', String(Math.round(progress)));
    progressNode?.classList.toggle('is-indeterminate', job.status === 'verifying');
    if (progressBar) progressBar.style.width = job.status === 'verifying' ? '' : `${progress}%`;
    setAppUpdateText('app-update-percent', job.status === 'verifying' ? 'SHA-256' : `${Math.round(progress)}%`);
    setAppUpdateText('app-update-stage', job.status === 'verifying' ? 'Dosya güvenliği doğrulanıyor…' : 'Güncelleme indiriliyor…');
    setAppUpdateText('app-update-transferred', `${formatUpdateBytes(job.downloaded)} / ${formatUpdateBytes(job.size)}`);
    setAppUpdateText('app-update-speed', job.speed_bps > 0 ? `${formatUpdateBytes(job.speed_bps)}/sn` : 'Hız ölçülüyor');
    setAppUpdateText('app-update-eta', job.status === 'verifying' ? 'Son kontrol' : formatUpdateDuration(job.eta_seconds));
    renderAppUpdateStatus(job.status === 'verifying' ? 'Dosya SHA-256 ile doğrulanıyor…' : `İndiriliyor… %${Math.round(progress)}`, progress, job.status);
}

async function pollAppUpdateDownload() {
    if (!appUpdateJobId) return;
    clearTimeout(appUpdatePollTimer);
    try {
        const response = await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(await readUpdateError(response, 'Güncelleme durumu alınamadı.'));
        const job = await response.json();
        renderAppUpdateJob(job);
        if (['downloading', 'verifying'].includes(job.status)) {
            setAppUpdateModalState(job.status);
            appUpdatePollTimer = setTimeout(pollAppUpdateDownload, 500);
            return;
        }
        if (job.ready_to_install) {
            setAppUpdateText('app-update-dialog-title', 'Güncelleme indirildi');
            setAppUpdateText('app-update-dialog-description', 'Dosyanın boyutu ve SHA-256 değeri doğrulandı. Hazır olduğunuzda MangaX yeniden başlatılarak güncellenecek.');
            setAppUpdateModalState('ready_to_install');
            renderAppUpdateStatus('Güncelleme doğrulandı ve kuruluma hazır.', 100, 'ready_to_install');
            document.getElementById('app-update-primary')?.focus();
            return;
        }
        if (['failed', 'cancelled'].includes(job.status)) {
            appUpdateCanResume = Boolean(job.can_resume);
            setAppUpdateModalState('failed', job.error || 'Güncelleme tamamlanamadı.');
            renderAppUpdateStatus(job.error || 'Güncelleme tamamlanamadı.', null, job.status);
        }
    } catch (error) {
        appUpdateCanResume = false;
        setAppUpdateModalState('failed', error.message || 'Güncelleme tamamlanamadı.');
        renderAppUpdateStatus(error.message || 'Güncelleme tamamlanamadı.', null, 'error');
    }
}

async function resumeAppUpdateDownload() {
    const response = await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}/resume`, { method: 'POST' });
    if (!response.ok) throw new Error(await readUpdateError(response, 'İndirme sürdürülemedi.'));
    appUpdateCanResume = false;
    setAppUpdateModalState('downloading');
    pollAppUpdateDownload();
}

async function installAppUpdate() {
    setAppUpdateModalState('restarting');
    setAppUpdateText('app-update-dialog-title', 'MangaX yeniden başlatılıyor');
    setAppUpdateText('app-update-dialog-description', 'Güncelleme sessiz biçimde kurulacak ve MangaX otomatik olarak yeniden açılacak.');
    renderAppUpdateStatus('Güvenli kurulum hazırlanıyor…', 100, 'restarting');
    const response = await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}/install`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true })
    });
    if (!response.ok) {
        const message = await readUpdateError(response, 'Kurulum başlatılamadı.');
        setAppUpdateModalState('failed', message);
        renderAppUpdateStatus(message, null, 'error');
    }
}

async function cancelAppUpdate() {
    if (!appUpdateJobId) return;
    clearTimeout(appUpdatePollTimer);
    try {
        await fetch(`/api/updates/download/${encodeURIComponent(appUpdateJobId)}`, { method: 'DELETE' });
    } finally {
        appUpdateJobId = '';
        appUpdateCanResume = false;
        setAppUpdateModalState('failed', 'Güncelleme kullanıcı tarafından iptal edildi.');
        renderAppUpdateStatus('Güncelleme iptal edildi.', null, 'cancelled');
    }
}

async function skipAppUpdateVersion() {
    if (!appUpdateDescriptor?.latest_version) return;
    const response = await fetch('/api/updates/skip', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: appUpdateDescriptor.latest_version })
    });
    if (!response.ok) return;
    renderAppUpdateStatus(`${appUpdateDescriptor.latest_version} sürümü atlandı.`, null, 'skipped');
    closeAppUpdateModal(true);
}

async function loadLastAppUpdateResult() {
    try {
        const response = await fetch('/api/updates/result?consume=true', { cache: 'no-store' });
        if (!response.ok) return;
        const result = await response.json();
        const runningVersion = document.body?.dataset.appVersion || '';
        const runningEdition = document.body?.dataset.appEdition || '';
        if (result.status === 'completed' && (result.version !== runningVersion || result.edition !== runningEdition)) {
            if (typeof showToast === 'function') showToast('Güncelleme doğrulanamadı. Yeniden kontrol edin.', 'error');
            return;
        }
        if (result.status === 'completed' && typeof showToast === 'function') showToast(result.message || 'MangaX güncellendi.', 'success');
        if (result.status === 'failed' && typeof showToast === 'function') showToast(result.message || 'Güncelleme tamamlanamadı.', 'error');
    } catch (_) {}
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('app-update-primary')?.addEventListener('click', async () => {
        try {
            if (appUpdateState === 'available') await startAppUpdateDownload(appUpdateDescriptor?.update_id || '');
            else if (appUpdateState === 'failed' && appUpdateCanResume) await resumeAppUpdateDownload();
            else if (appUpdateState === 'ready_to_install') await installAppUpdate();
        } catch (error) {
            setAppUpdateModalState('failed', error.message || 'Güncelleme işlemi tamamlanamadı.');
        }
    });
    document.getElementById('app-update-later')?.addEventListener('click', () => closeAppUpdateModal());
    document.getElementById('app-update-close')?.addEventListener('click', () => closeAppUpdateModal());
    document.getElementById('app-update-skip')?.addEventListener('click', skipAppUpdateVersion);
    document.getElementById('app-update-overlay')?.addEventListener('click', event => {
        if (event.target === event.currentTarget) closeAppUpdateModal();
    });
});

document.addEventListener('keydown', event => {
    const overlay = document.getElementById('app-update-overlay');
    if (!overlay?.classList.contains('active')) return;
    if (event.key === 'Escape' && appUpdateState !== 'restarting') {
        event.preventDefault();
        closeAppUpdateModal();
    } else if (event.key === 'Tab') {
        const focusable = [...overlay.querySelectorAll('button:not([disabled]):not(.hidden), [href], input:not([disabled])')]
            .filter(node => node.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
});

window.addEventListener('beforeunload', event => {
    if (!['downloading', 'verifying', 'ready_to_install'].includes(appUpdateState)) return;
    event.preventDefault();
    event.returnValue = '';
});
