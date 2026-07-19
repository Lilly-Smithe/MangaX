// "bingus" yalnızca gizli paneli açan bir Easter egg'dir; yetki vermez.
// Gerçek erişim her zaman GitHub cihaz akışı ve private depo izniyle doğrulanır.
const EDITION_GATE_CLICK_COUNT = 10;
const EDITION_GATE_CLICK_WINDOW_MS = 4000;
const EDITION_GATE_PASSWORD = 'bingus';
const EDITION_GATE_MAX_COOLDOWN_MS = 8000;

let editionGateClicks = [];
let editionGateClickResetTimer = null;
let editionGateWrongAttempts = 0;
let editionGateLastWrongAt = 0;
let editionGateCooldownUntil = 0;
let editionGateCooldownTimer = null;
let editionGatePreviousFocus = null;
let editionGatePollGeneration = 0;
let editionGateActiveRequestId = '';
let editionFullReleaseManifest = null;
let editionFullReleaseJobId = '';
let editionFullReleasePollGeneration = 0;
let editionFullReleaseReadyNotifiedJobId = '';

function isEditionGateTriggerAvailable() {
    return document.body?.dataset.appEdition === 'reader'
        && Boolean(document.querySelector('.nav-btn[data-tab="library"]'));
}

function handleEditionGateLibraryActivation() {
    if (!isEditionGateTriggerAvailable()) return;
    const now = Date.now();
    if (!editionGateClicks.length) {
        clearTimeout(editionGateClickResetTimer);
        editionGateClickResetTimer = setTimeout(() => {
            editionGateClicks = [];
            editionGateClickResetTimer = null;
        }, EDITION_GATE_CLICK_WINDOW_MS);
    }
    editionGateClicks.push(now);
    editionGateClicks = editionGateClicks.filter(value => now - value <= EDITION_GATE_CLICK_WINDOW_MS);
    if (editionGateClicks.length < EDITION_GATE_CLICK_COUNT) return;
    editionGateClicks = [];
    clearTimeout(editionGateClickResetTimer);
    editionGateClickResetTimer = null;
    openEditionSecretModal();
}

function editionGateFocusableElements(modal) {
    return [...modal.querySelectorAll('button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])')]
        .filter(element => !element.classList.contains('hidden'));
}

function openEditionGateModal(modal, focusTarget) {
    editionGatePreviousFocus = document.activeElement;
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => focusTarget?.focus());
}

function closeEditionGateModal(modal) {
    if (!modal || modal.classList.contains('hidden')) return;
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    if (editionGatePreviousFocus && typeof editionGatePreviousFocus.focus === 'function') {
        editionGatePreviousFocus.focus();
    }
    editionGatePreviousFocus = null;
}

function openEditionSecretModal() {
    const modal = document.getElementById('edition-secret-modal');
    const input = document.getElementById('edition-secret-input');
    const error = document.getElementById('edition-secret-error');
    if (!modal || !input) return;
    input.value = '';
    if (error) error.textContent = '';
    updateEditionGateCooldown();
    openEditionGateModal(modal, input);
}

function closeEditionSecretModal() {
    closeEditionGateModal(document.getElementById('edition-secret-modal'));
}

function updateEditionGateCooldown() {
    const button = document.getElementById('edition-secret-submit');
    const error = document.getElementById('edition-secret-error');
    const remaining = Math.max(0, editionGateCooldownUntil - Date.now());
    if (button) button.disabled = remaining > 0;
    if (remaining > 0 && error) error.textContent = 'Lütfen kısa süre bekleyin.';
    if (remaining <= 0 && error?.textContent === 'Lütfen kısa süre bekleyin.') error.textContent = '';
    clearTimeout(editionGateCooldownTimer);
    if (remaining > 0) {
        editionGateCooldownTimer = setTimeout(updateEditionGateCooldown, Math.min(remaining, 250));
    }
}

function submitEditionSecret(event) {
    event.preventDefault();
    const input = document.getElementById('edition-secret-input');
    const error = document.getElementById('edition-secret-error');
    if (Date.now() < editionGateCooldownUntil) {
        updateEditionGateCooldown();
        return;
    }
    if (input?.value === EDITION_GATE_PASSWORD) {
        editionGateWrongAttempts = 0;
        editionGateCooldownUntil = 0;
        closeEditionSecretModal();
        openEditionAccessPanel();
        return;
    }

    const wrongAt = Date.now();
    if (wrongAt - editionGateLastWrongAt > 30000) editionGateWrongAttempts = 0;
    editionGateLastWrongAt = wrongAt;
    editionGateWrongAttempts += 1;
    const cooldown = editionGateWrongAttempts >= 2
        ? Math.min(EDITION_GATE_MAX_COOLDOWN_MS, (editionGateWrongAttempts - 1) * 2000)
        : 0;
    editionGateCooldownUntil = Date.now() + cooldown;
    if (input) {
        input.value = '';
        input.focus();
    }
    if (error) error.textContent = 'Doğrulama başarısız. Lütfen tekrar deneyin.';
    updateEditionGateCooldown();
}

function openEditionAccessPanel() {
    const modal = document.getElementById('edition-access-modal');
    const title = document.getElementById('edition-access-title');
    if (!modal || !title) return;
    title.textContent = 'GitHub hesabını bağla';
    openEditionGateModal(modal, modal.querySelector('.edition-gate-close'));
    loadEditionAccessStatus();
}

function closeEditionAccessPanel() {
    const downloadContinues = Boolean(editionFullReleaseJobId);
    editionGatePollGeneration += 1;
    editionFullReleasePollGeneration += 1;
    cancelEditionAccessRequest({ silent: true });
    closeEditionGateModal(document.getElementById('edition-access-modal'));
    if (downloadContinues) {
        showToast('MangaX Full indirmesi arka planda devam ediyor.', 'info');
    }
}

function renderEditionAccessContent({ message, state = '', action = null, code = '', showDescription = true }) {
    const content = document.getElementById('edition-access-content');
    if (!content) return;
    content.innerHTML = '';
    const copy = document.createElement('p');
    copy.className = 'edition-access-copy';
    copy.textContent = 'Hesap ve depo yetkisi tarayıcı üzerinden güvenli biçimde doğrulanır.';
    const status = document.createElement('div');
    status.className = `edition-access-status ${state}`.trim();
    status.textContent = message;
    if (showDescription) content.appendChild(copy);
    content.appendChild(status);
    if (code) {
        const codeBox = document.createElement('div');
        codeBox.className = 'edition-access-code';
        codeBox.textContent = 'Cihaz kodu';
        const strong = document.createElement('strong');
        strong.textContent = code;
        codeBox.appendChild(strong);
        content.appendChild(codeBox);
    }
    if (action) {
        const actions = document.createElement('div');
        actions.className = 'edition-access-actions';
        const button = document.createElement('button');
        button.className = 'btn btn-primary';
        button.type = 'button';
        button.textContent = action.label;
        button.addEventListener('click', action.handler);
        actions.appendChild(button);
        content.appendChild(actions);
    }
}

async function readEditionAccessResponse(response) {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload.detail || 'Bağlantı işlemi tamamlanamadı.');
        error.code = response.headers.get('X-MangaX-GitHub-Error')
            || response.headers.get('X-MangaX-Full-Release-Error')
            || 'github_error';
        throw error;
    }
    return payload;
}

function renderEditionAccessError(error, retryHandler = connectEditionAccess) {
    if (error?.code === 'access_not_found') {
        renderEditionAccessContent({
            message: 'Bu hesap için erişim bulunamadı',
            state: 'error',
            showDescription: false,
            action: { label: 'Farklı Hesapla Dene', handler: retryHandler },
        });
        return;
    }
    if (error?.code === 'connection_cancelled') {
        renderEditionAccessContent({
            message: 'Bağlantı iptal edildi.',
            action: { label: 'Tekrar Dene', handler: retryHandler },
        });
        return;
    }
    if (error?.code === 'connection_expired') {
        renderEditionAccessContent({
            message: 'Bağlantı süresi doldu.',
            action: { label: 'Yeni Kod Al', handler: retryHandler },
        });
        return;
    }
    renderEditionAccessContent({
        message: error?.message || 'GitHub bağlantısı sırasında bir hata oluştu.',
        state: 'error',
        action: { label: 'Tekrar Dene', handler: retryHandler },
    });
}

async function loadEditionAccessStatus() {
    renderEditionAccessContent({ message: 'Hesap durumu kontrol ediliyor…' });
    try {
        const status = await readEditionAccessResponse(await fetch('/api/integrations/github/status', { cache: 'no-store' }));
        if (status.authorized) {
            await loadEditionFullRelease(status.username || 'GitHub hesabı');
        } else if (!status.configured) {
            renderEditionAccessContent({ message: 'Hesap bağlantısı henüz yapılandırılmamış.' });
        } else if (status.error_code === 'access_not_found') {
            renderEditionAccessError({ code: 'access_not_found' });
        } else {
            renderEditionAccessContent({
                message: status.error || 'Bağlı ve yetkili bir hesap bulunmuyor.',
                action: { label: 'GitHub ile Bağla', handler: connectEditionAccess },
            });
        }
    } catch (error) {
        renderEditionAccessError(error, loadEditionAccessStatus);
    }
}

async function connectEditionAccess() {
    renderEditionAccessContent({ message: 'Bağlantı başlatılıyor…' });
    try {
        const started = await readEditionAccessResponse(await fetch('/api/integrations/github/connect', { method: 'POST' }));
        const generation = ++editionGatePollGeneration;
        editionGateActiveRequestId = started.request_id || '';
        renderEditionAccessContent({
            message: 'Tarayıcıdaki onay tamamlanana kadar bu pencereyi açık tutun.',
            code: started.user_code || '',
            action: { label: 'İptal Et', handler: cancelEditionAccessRequest },
        });
        await pollEditionAccess(started.request_id, Number(started.interval) || 5, generation);
    } catch (error) {
        renderEditionAccessError(error);
    }
}

async function pollEditionAccess(requestId, intervalSeconds, generation) {
    if (!requestId || generation !== editionGatePollGeneration) return;
    await new Promise(resolve => setTimeout(resolve, Math.max(1, intervalSeconds) * 1000));
    if (generation !== editionGatePollGeneration) return;
    try {
        const status = await readEditionAccessResponse(await fetch(
            `/api/integrations/github/connect/status?request_id=${encodeURIComponent(requestId)}`,
            { cache: 'no-store' }
        ));
        if (status.status === 'pending') {
            await pollEditionAccess(requestId, Number(status.retry_after) || intervalSeconds, generation);
        } else if (status.authorized) {
            editionGateActiveRequestId = '';
            await loadEditionFullRelease(status.username || 'GitHub hesabı');
        } else {
            editionGateActiveRequestId = '';
            await loadEditionAccessStatus();
        }
    } catch (error) {
        editionGateActiveRequestId = '';
        renderEditionAccessError(error);
    }
}

function formatEditionReleaseBytes(bytes) {
    const value = Math.max(0, Number(bytes) || 0);
    if (value < 1024) return `${value} B`;
    const units = ['KB', 'MB', 'GB'];
    let size = value / 1024;
    let unit = units[0];
    for (let index = 1; index < units.length && size >= 1024; index += 1) {
        size /= 1024;
        unit = units[index];
    }
    return `${size.toFixed(size >= 100 ? 0 : 1)} ${unit}`;
}

function appendEditionReleaseButton(container, label, handler, secondary = false) {
    const button = document.createElement('button');
    button.className = secondary ? 'btn btn-secondary' : 'btn btn-primary';
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', handler);
    container.appendChild(button);
}

function renderEditionFullRelease(manifest, username) {
    const content = document.getElementById('edition-access-content');
    if (!content) return;
    content.innerHTML = '';
    const status = document.createElement('div');
    status.className = 'edition-access-status success';
    status.textContent = `${username} için erişim doğrulandı.`;
    const title = document.createElement('strong');
    title.className = 'edition-release-title';
    title.textContent = 'MangaX Full kurulumu hazır';
    const details = document.createElement('dl');
    details.className = 'edition-release-details';
    [
        ['Sürüm', manifest.version],
        ['Dosya boyutu', formatEditionReleaseBytes(manifest.size)],
        ['SHA-256', manifest.sha256],
    ].forEach(([label, value]) => {
        const term = document.createElement('dt');
        term.textContent = label;
        const description = document.createElement('dd');
        description.textContent = value;
        details.append(term, description);
    });
    const note = document.createElement('p');
    note.className = 'edition-access-copy';
    note.textContent = 'İndirme yalnızca onayınızla başlar. Dosya çalıştırılmadan önce SHA-256 değeri doğrulanır.';
    const actions = document.createElement('div');
    actions.className = 'edition-access-actions';
    appendEditionReleaseButton(actions, 'Full Sürümü İndir', confirmEditionFullReleaseDownload);
    appendEditionReleaseButton(actions, 'Bağlantıyı Kaldır', disconnectEditionAccess, true);
    content.append(status, title, details, note, actions);
}

async function loadEditionFullRelease(username = 'GitHub hesabı') {
    if (editionFullReleaseJobId) {
        try {
            const existingJob = await readEditionAccessResponse(await fetch(
                `/api/integrations/github/full-release/download/${encodeURIComponent(editionFullReleaseJobId)}`,
                { cache: 'no-store' }
            ));
            if (existingJob.status === 'downloading' || existingJob.status === 'ready') {
                const generation = ++editionFullReleasePollGeneration;
                renderEditionFullReleaseProgress(existingJob);
                if (existingJob.status === 'downloading') pollEditionFullReleaseDownload(generation);
                return;
            }
            editionFullReleaseJobId = '';
        } catch (_error) {
            editionFullReleaseJobId = '';
        }
    }
    renderEditionAccessContent({ message: 'Full sürüm bilgisi kontrol ediliyor…' });
    try {
        const manifest = await readEditionAccessResponse(await fetch(
            '/api/integrations/github/full-release',
            { cache: 'no-store' }
        ));
        editionFullReleaseManifest = manifest;
        renderEditionFullRelease(manifest, username);
    } catch (error) {
        const content = document.getElementById('edition-access-content');
        if (!content) return;
        content.innerHTML = '';
        const status = document.createElement('div');
        status.className = 'edition-access-status error';
        status.textContent = error?.message || 'Full sürüm bilgisi alınamadı.';
        const actions = document.createElement('div');
        actions.className = 'edition-access-actions';
        appendEditionReleaseButton(actions, 'Yeniden Dene', () => loadEditionFullRelease(username));
        appendEditionReleaseButton(actions, 'Bağlantıyı Kaldır', disconnectEditionAccess, true);
        content.append(status, actions);
    }
}

async function confirmEditionFullReleaseDownload() {
    const manifest = editionFullReleaseManifest;
    if (!manifest) return;
    const accepted = await showAppConfirm({
        title: 'MangaX Full Sürümünü İndir',
        message: `${manifest.version} sürümü (${formatEditionReleaseBytes(manifest.size)}) geçici klasöre indirilecek ve SHA-256 ile doğrulanacak. İndirme başlatılsın mı?`,
        confirmText: 'İndirmeyi Başlat',
        cancelText: 'Vazgeç',
        icon: 'fa-download',
    });
    if (!accepted) return;
    renderEditionAccessContent({ message: 'Güvenli indirme hazırlanıyor…' });
    try {
        const job = await readEditionAccessResponse(await fetch(
            '/api/integrations/github/full-release/download',
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ manifest_id: manifest.manifest_id, confirmed: true }),
            }
        ));
        editionFullReleaseJobId = job.job_id || '';
        const generation = ++editionFullReleasePollGeneration;
        renderEditionFullReleaseProgress(job);
        pollEditionFullReleaseDownload(generation);
    } catch (error) {
        renderEditionAccessError(error, confirmEditionFullReleaseDownload);
    }
}

function renderEditionFullReleaseProgress(job) {
    const content = document.getElementById('edition-access-content');
    if (!content) return;
    content.innerHTML = '';
    const status = document.createElement('div');
    status.className = `edition-access-status ${job.status === 'ready' ? 'success' : ''}`.trim();
    status.textContent = job.status === 'ready'
        ? 'İndirme tamamlandı. MangaX Full güvenle kurulmaya hazır.'
        : `İndiriliyor… %${Number(job.progress || 0).toFixed(1)}`;
    const progress = document.createElement('div');
    progress.className = 'edition-release-progress';
    const bar = document.createElement('span');
    bar.style.width = `${Math.max(0, Math.min(100, Number(job.progress) || 0))}%`;
    progress.appendChild(bar);
    const copy = document.createElement('p');
    copy.className = 'edition-access-copy';
    copy.textContent = `${formatEditionReleaseBytes(job.downloaded)} / ${formatEditionReleaseBytes(job.size)}`;
    const actions = document.createElement('div');
    actions.className = 'edition-access-actions';
    if (job.ready_to_install) {
        appendEditionReleaseButton(actions, 'MangaX Full’a Yükselt', confirmEditionFullReleaseInstall);
        appendEditionReleaseButton(actions, 'Dosyayı Sil', cancelEditionFullReleaseDownload, true);
    } else {
        appendEditionReleaseButton(actions, 'İndirmeyi İptal Et', cancelEditionFullReleaseDownload, true);
    }
    content.append(status, progress, copy, actions);
}

async function pollEditionFullReleaseDownload(generation) {
    if (!editionFullReleaseJobId || generation !== editionFullReleasePollGeneration) return;
    await new Promise(resolve => setTimeout(resolve, 500));
    if (generation !== editionFullReleasePollGeneration) return;
    try {
        const job = await readEditionAccessResponse(await fetch(
            `/api/integrations/github/full-release/download/${encodeURIComponent(editionFullReleaseJobId)}`,
            { cache: 'no-store' }
        ));
        renderEditionFullReleaseProgress(job);
        if (job.status === 'downloading') {
            pollEditionFullReleaseDownload(generation);
        } else if (job.status === 'ready') {
            if (editionFullReleaseReadyNotifiedJobId !== job.job_id) {
                editionFullReleaseReadyNotifiedJobId = job.job_id;
                showToast('MangaX Full indirildi ve doğrulandı. Yükseltmeye hazır.', 'success');
            }
        } else if (job.status !== 'ready') {
            editionFullReleaseJobId = '';
            renderEditionAccessContent({
                message: job.error || 'Full sürüm indirilemedi.',
                state: 'error',
                action: { label: 'Yeniden Dene', handler: confirmEditionFullReleaseDownload },
            });
        }
    } catch (error) {
        editionFullReleaseJobId = '';
        renderEditionAccessError(error, confirmEditionFullReleaseDownload);
    }
}

async function cancelEditionFullReleaseDownload({ silent = false } = {}) {
    const jobId = editionFullReleaseJobId;
    if (!jobId) return;
    editionFullReleaseJobId = '';
    editionFullReleasePollGeneration += 1;
    editionFullReleaseReadyNotifiedJobId = '';
    try {
        await readEditionAccessResponse(await fetch(
            `/api/integrations/github/full-release/download/${encodeURIComponent(jobId)}`,
            { method: 'DELETE' }
        ));
        if (!silent) await loadEditionFullRelease();
    } catch (error) {
        if (!silent) renderEditionAccessError(error, loadEditionFullRelease);
    }
}

async function confirmEditionFullReleaseInstall() {
    if (!editionFullReleaseJobId) return;
    const accepted = await showAppConfirm({
        title: 'MangaX Full’a Yükselt',
        message: 'Kütüphane, ayarlar ve okuma geçmişi korunacak. Full sürüm mevcut MangaX kurulumunun bulunduğu klasöre kurulacak ve eski Reader kısayolu kaldırılacak.',
        confirmText: 'Yükseltmeyi Başlat',
        cancelText: 'Şimdi Değil',
        icon: 'fa-shield-halved',
    });
    if (!accepted) return;
    const jobId = editionFullReleaseJobId;
    renderEditionAccessContent({ message: 'Reader kapatılıyor ve doğrulanmış kurulum hazırlanıyor…' });
    try {
        await readEditionAccessResponse(await fetch(
            `/api/integrations/github/full-release/download/${encodeURIComponent(jobId)}/install`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirmed: true }),
            }
        ));
    } catch (error) {
        renderEditionAccessError(error, confirmEditionFullReleaseInstall);
    }
}

async function cancelEditionAccessRequest({ silent = false } = {}) {
    const requestId = editionGateActiveRequestId;
    if (!requestId) return;
    editionGateActiveRequestId = '';
    editionGatePollGeneration += 1;
    try {
        await readEditionAccessResponse(await fetch(
            `/api/integrations/github/connect/${encodeURIComponent(requestId)}`,
            { method: 'DELETE' }
        ));
        if (!silent) {
            renderEditionAccessContent({
                message: 'Bağlantı iptal edildi.',
                action: { label: 'Tekrar Dene', handler: connectEditionAccess },
            });
        }
    } catch (error) {
        if (!silent) renderEditionAccessError(error);
    }
}

async function disconnectEditionAccess() {
    editionGatePollGeneration += 1;
    editionGateActiveRequestId = '';
    renderEditionAccessContent({ message: 'Bağlantı kaldırılıyor…' });
    try {
        await readEditionAccessResponse(await fetch('/api/integrations/github/connection', { method: 'DELETE' }));
        renderEditionAccessContent({
            message: 'Bağlantı kaldırıldı.',
            action: { label: 'GitHub ile Bağla', handler: connectEditionAccess },
        });
    } catch (error) {
        renderEditionAccessError(error, disconnectEditionAccess);
    }
}

function handleEditionGateKeydown(event) {
    const activeModal = ['edition-secret-modal', 'edition-access-modal']
        .map(id => document.getElementById(id))
        .find(modal => modal && !modal.classList.contains('hidden'));
    if (!activeModal) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        activeModal.id === 'edition-secret-modal' ? closeEditionSecretModal() : closeEditionAccessPanel();
        return;
    }
    if (event.key !== 'Tab') return;
    const focusable = editionGateFocusableElements(activeModal);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
}

if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
    if (!isEditionGateTriggerAvailable()) return;
    const libraryTrigger = document.querySelector('.nav-btn[data-tab="library"]');
    if (!libraryTrigger) return;
    libraryTrigger.addEventListener('click', handleEditionGateLibraryActivation);
    document.getElementById('edition-secret-form')?.addEventListener('submit', submitEditionSecret);
    document.querySelectorAll('[data-edition-gate-close="secret"]').forEach(button => button.addEventListener('click', closeEditionSecretModal));
    document.querySelectorAll('[data-edition-gate-close="access"]').forEach(button => button.addEventListener('click', closeEditionAccessPanel));
    document.addEventListener('keydown', handleEditionGateKeydown);
});
