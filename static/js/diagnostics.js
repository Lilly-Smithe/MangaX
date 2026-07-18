let systemCheckRunning = false;
let systemCheckMode = 'quick';
let systemCheckResults = [];
let systemCheckSourceResults = [];

const systemCheckStatusMeta = {
    healthy: { label: 'Sağlıklı', icon: 'fa-circle-check' },
    warning: { label: 'Uyarı', icon: 'fa-triangle-exclamation' },
    broken: { label: 'Hata', icon: 'fa-circle-xmark' },
    timeout: { label: 'Zaman aşımı', icon: 'fa-clock' },
};

function normalizeSystemCheckStatus(status) {
    return Object.prototype.hasOwnProperty.call(systemCheckStatusMeta, status) ? status : 'warning';
}

function setSystemCheckProgress(current, total, label) {
    const progress = document.getElementById('system-check-progress');
    const progressLabel = document.getElementById('system-check-progress-label');
    const progressValue = document.getElementById('system-check-progress-value');
    const progressBar = document.getElementById('system-check-progress-bar');
    const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
    progress?.classList.remove('hidden');
    if (progressLabel) progressLabel.textContent = label;
    if (progressValue) progressValue.textContent = `${percent}%`;
    if (progressBar) progressBar.style.width = `${percent}%`;
}

function systemCheckResultMarkup(item, kind = 'core') {
    const status = normalizeSystemCheckStatus(item.status);
    const meta = systemCheckStatusMeta[status];
    const eyebrow = kind === 'source' ? 'KAYNAK' : 'SİSTEM';
    return `
        <article class="system-check-result ${status}">
            <span class="system-check-result-icon"><i class="fa-solid ${meta.icon}"></i></span>
            <div class="system-check-result-copy">
                <span>${eyebrow} · ${meta.label}</span>
                <strong>${escapeHtml(item.label || item.name || item.source_id || 'Kontrol')}</strong>
                <small>${escapeHtml(item.message || 'Ayrıntı bulunmuyor.')}</small>
            </div>
        </article>
    `;
}

function renderSystemCheckResults() {
    const container = document.getElementById('system-check-results');
    const summary = document.getElementById('system-check-summary');
    if (!container || !summary) return;

    const combined = [
        ...systemCheckResults.map(item => ({ ...item, kind: 'core' })),
        ...systemCheckSourceResults.map(item => ({ ...item, kind: 'source' })),
    ];
    if (!combined.length) return;

    const counts = combined.reduce((acc, item) => {
        const status = normalizeSystemCheckStatus(item.status);
        acc[status] = (acc[status] || 0) + 1;
        return acc;
    }, { healthy: 0, warning: 0, broken: 0, timeout: 0 });
    const problemCount = counts.warning + counts.broken + counts.timeout;
    const summaryStatus = counts.broken > 0 ? 'broken' : problemCount > 0 ? 'warning' : 'healthy';
    summary.className = `system-check-summary ${summaryStatus}`;
    summary.innerHTML = `
        <div><i class="fa-solid ${systemCheckStatusMeta[summaryStatus].icon}"></i><strong>${problemCount ? `${problemCount} konu dikkat istiyor` : 'MangaX sağlıklı görünüyor'}</strong></div>
        <span>${counts.healthy} sağlıklı · ${counts.warning} uyarı · ${counts.broken + counts.timeout} hata</span>
    `;
    container.innerHTML = combined.map(item => systemCheckResultMarkup(item, item.kind)).join('');
}

async function readSystemCheckResponse(response, fallback) {
    let payload = {};
    try {
        payload = await response.json();
    } catch (_) { /* aşağıdaki ortak hata kullanılır */ }
    if (!response.ok) throw new Error(payload.detail || fallback);
    return payload;
}



async function runSystemCheck() {
    if (systemCheckRunning) return;
    systemCheckRunning = true;
    systemCheckMode = 'quick';
    systemCheckResults = [];
    systemCheckSourceResults = [];
    const quickButton = document.getElementById('system-check-quick-btn');
    const fullButton = document.getElementById('system-check-full-btn');
    const reportButton = document.getElementById('system-check-report-btn');
    const reportPath = document.getElementById('system-check-report-path');
    const container = document.getElementById('system-check-results');
    [quickButton, fullButton].forEach(button => { if (button) button.disabled = true; });
    if (reportButton) reportButton.disabled = true;
    reportPath?.classList.add('hidden');
    if (container) container.innerHTML = '<div class="system-check-empty loading"><i class="fa-solid fa-spinner fa-spin"></i><strong>Sistem kontrol ediliyor</strong><span>Yerel Reader bileşenleri sınanıyor…</span></div>';
    setSystemCheckProgress(0, 1, 'Yerel kontroller çalıştırılıyor…');
    try {
        const response = await fetch('/api/diagnostics/quick?local_only=true', { cache: 'no-store' });
        const payload = await readSystemCheckResponse(response, 'Sistem kontrolü başlatılamadı.');
        systemCheckResults = Array.isArray(payload.checks) ? payload.checks : [];
        renderSystemCheckResults();
        setSystemCheckProgress(1, 1, 'Hızlı kontrol tamamlandı.');
        if (reportButton) reportButton.disabled = false;
        const problemCount = systemCheckResults.filter(item => normalizeSystemCheckStatus(item.status) !== 'healthy').length;
        showToast(problemCount ? `Sistem kontrolü tamamlandı: ${problemCount} konu dikkat istiyor.` : 'Sistem kontrolü tamamlandı; sorun bulunmadı.', problemCount ? 'info' : 'success');
    } catch (error) {
        if (container) container.innerHTML = `<div class="system-check-empty error"><strong>Kontrol tamamlanamadı</strong><span>${escapeHtml(error.message || 'Bilinmeyen hata')}</span></div>`;
        document.getElementById('system-check-progress')?.classList.add('hidden');
        showToast('Sistem kontrolü tamamlanamadı.', 'error');
    } finally {
        systemCheckRunning = false;
        [quickButton, fullButton].forEach(button => { if (button) button.disabled = false; });
    }
}

async function saveSystemCheckReport() {
    const button = document.getElementById('system-check-report-btn');
    const path = document.getElementById('system-check-report-path');
    if (!systemCheckResults.length || !button) return;
    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Kaydediliyor';
    try {
        const response = await fetch('/api/diagnostics/report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode: systemCheckMode,
                checks: systemCheckResults,
                sources: systemCheckSourceResults,
            }),
        });
        const payload = await readSystemCheckResponse(response, 'Rapor kaydedilemedi.');
        if (path) {
            path.textContent = payload.path || payload.filename || 'Rapor kaydedildi.';
            path.classList.remove('hidden');
        }
        showToast('Anonim sistem raporu kaydedildi.', 'success');
    } catch (error) {
        showToast(error.message || 'Rapor kaydedilemedi.', 'error');
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fa-solid fa-file-lines"></i> Raporu Kaydet';
    }
}
