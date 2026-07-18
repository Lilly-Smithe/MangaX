// Yerel manga içe aktarma: masaüstü dosya seçicisi ile çalışır.
let localImportBusy = false;
let activeLocalImportJobId = '';

function openLocalImportDialog() {
    const dialog = document.getElementById('local-import-dialog');
    if (!dialog) return;
    dialog.classList.remove('hidden');
    dialog.setAttribute('aria-hidden', 'false');
}

function closeLocalImportDialog() {
    if (localImportBusy) return;
    const dialog = document.getElementById('local-import-dialog');
    dialog?.classList.add('hidden');
    dialog?.setAttribute('aria-hidden', 'true');
}

async function startLocalMangaImport(selectionType) {
    if (localImportBusy) return;
    const bridge = window.pywebview?.api;
    if (!bridge?.start_local_manga_import) {
        showToast('Manga ekleme, MangaX masaüstü uygulamasında kullanılabilir.', 'warning');
        return;
    }
    localImportBusy = true;
    const progress = document.getElementById('local-import-progress');
    progress?.classList.remove('hidden');
    document.querySelectorAll('.local-import-options button').forEach(button => { button.disabled = true; });
    let completed = false;
    try {
        let result = await bridge.start_local_manga_import(selectionType);
        if (!result || result.status === 'cancelled') return;
        if (result.status === 'error') throw new Error(result.message || 'Manga eklenemedi.');
        activeLocalImportJobId = result.job_id;
        while (['queued', 'running'].includes(result.status)) {
            renderLocalImportProgress(result);
            await new Promise(resolve => setTimeout(resolve, 250));
            result = await bridge.get_local_manga_import(activeLocalImportJobId);
        }
        renderLocalImportProgress(result);
        if (result.status === 'cancelled') {
            showToast('Manga ekleme iptal edildi.', 'info');
            return;
        }
        if (result.status !== 'success') throw new Error(result.message || 'Manga eklenemedi.');
        await loadLibrary({ silent: true });
        switchLibraryView('catalog');
        completed = true;
        showToast(`${result.manga?.title || 'Manga'} kütüphaneye eklendi.`, 'success');
    } catch (error) {
        showToast(error.message || 'Manga eklenirken bir hata oluştu.', 'error');
    } finally {
        activeLocalImportJobId = '';
        localImportBusy = false;
        progress?.classList.add('hidden');
        document.querySelectorAll('.local-import-options button').forEach(button => { button.disabled = false; });
        if (completed) closeLocalImportDialog();
    }
}

function renderLocalImportProgress(job) {
    const percent = Math.max(0, Math.min(100, Number(job?.progress) || 0));
    const message = document.getElementById('local-import-progress-message');
    const label = document.getElementById('local-import-progress-percent');
    const bar = document.getElementById('local-import-progress-bar');
    if (message) message.textContent = job?.message || 'Manga hazırlanıyor…';
    if (label) label.textContent = `${percent}%`;
    if (bar) bar.style.width = `${percent}%`;
}

async function cancelLocalMangaImport() {
    if (!activeLocalImportJobId) return;
    const bridge = window.pywebview?.api;
    await bridge?.cancel_local_manga_import?.(activeLocalImportJobId);
}

document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeLocalImportDialog();
});
