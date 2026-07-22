// Library Management: online reading history and offline downloads.
const deletingLibraryMangaIds = new Set();
let libraryBulkDeleteInFlight = false;

function isReaderEdition() {
    return document.body?.dataset.appEdition === 'reader';
}

function libraryRemoveButtonMarkup(manga) {
    const title = String(manga?.title || 'Manga');
    return `
        <button class="library-delete-btn" type="button" title="Kütüphaneden kaldır"
                aria-label="${escapeHtml(title)} mangasını kütüphaneden kaldır">
            <i class="fa-solid fa-trash-can" aria-hidden="true"></i>
        </button>
    `;
}

function setLibraryRemoveBusy(mangaId, busy) {
    document.querySelectorAll('.library-manga-card[data-manga-id], .library-catalog-card[data-manga-id]')
        .forEach(card => {
            if (card.dataset.mangaId !== mangaId) return;
            card.classList.toggle('library-remove-pending', busy);
            const button = card.querySelector('.library-delete-btn');
            if (button) button.disabled = busy;
        });
}

function configureLibraryEditionLayout() {
    if (isReaderEdition()) return;
    document.getElementById('library-continue-btn')?.remove();
    document.getElementById('library-continue-panel')?.remove();
    if (activeLibraryView === 'continue') activeLibraryView = 'catalog';
}

function switchLibraryView(view) {
    const fallbackView = isReaderEdition() ? 'continue' : 'catalog';
    if (view === 'downloaded' && (typeof isGithubExtensionsAvailable !== 'function' || !isGithubExtensionsAvailable())) {
        view = fallbackView;
    }
    if (!isReaderEdition() && view === 'continue') view = 'catalog';
    activeLibraryView = ['continue', 'catalog', 'downloaded'].includes(view) ? view : fallbackView;
    ['continue', 'catalog', 'downloaded'].forEach(name => {
        document.getElementById(`library-${name}-btn`)?.classList.toggle('active', name === activeLibraryView);
        document.getElementById(`library-${name}-panel`)?.classList.toggle('active', name === activeLibraryView);
    });
    if (activeLibraryView === 'downloaded') updateDownloadStatus();
}

function formatStorageBytes(value) {
    const bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function hasReadableLocalChapters(manga) {
    return Object.values(manga?.downloaded_chapters || {}).some(chapter => (
        Number(chapter?.page_count) > 0
        || (Array.isArray(chapter?.pages) && chapter.pages.length > 0)
    ));
}

function hasReadableLocalChapter(manga, chapterId) {
    const chapter = manga?.downloaded_chapters?.[chapterId];
    return Boolean(chapter) && (
        Number(chapter.page_count) > 0
        || (Array.isArray(chapter.pages) && chapter.pages.length > 0)
    );
}

function getMangaDownloadSummary(mangaId) {
    const items = Object.values(chapterDownloadStatus).filter(item => item.manga_id === mangaId);
    const active = items.filter(item => ['pending', 'downloading', 'paused'].includes(item.status));
    if (!active.length) return null;
    const total = active.reduce((sum, item) => sum + (Number(item.total) || 0), 0);
    const progress = active.reduce((sum, item) => sum + (Number(item.progress) || 0), 0);
    return {
        count: active.length,
        percent: total > 0 ? Math.round((progress / total) * 100) : 0,
        paused: active.every(item => item.status === 'paused'),
    };
}

function updateLibraryCardDownloadStates() {
    if (typeof isGithubExtensionsAvailable === 'function' && !isGithubExtensionsAvailable()) {
        document.querySelectorAll('.library-card-download-state').forEach(target => target.classList.add('hidden'));
        return;
    }
    document.querySelectorAll('.library-manga-card[data-manga-id]').forEach(card => {
        const target = card.querySelector('.library-card-download-state');
        if (!target) return;
        const summary = getMangaDownloadSummary(card.dataset.mangaId);
        target.classList.toggle('hidden', !summary);
        if (!summary) return;
        target.innerHTML = `
            <div><span>${summary.paused ? 'Duraklatıldı' : `${summary.count} bölüm indiriliyor`}</span><strong>%${summary.percent}</strong></div>
            <div class="library-card-download-track"><span style="width:${summary.percent}%"></span></div>
        `;
    });
}

function buildLibraryCard(id, manga, mode) {
    const chapters = manga.downloaded_chapters || {};
    const downloadedCount = Object.keys(chapters).length;
    const lastChapter = chapters[manga.last_read_chapter] || null;
    const chapterNum = manga.last_read_chapter_num || lastChapter?.chapter || '?';
    const coverSrc = manga.cover_local_url || (manga.cover_path
        ? '/' + manga.cover_path.split('/').map(segment => encodeURIComponent(segment)).join('/')
        : manga.cover_url);
    const isContinue = mode === 'continue';
    const canReadOffline = hasReadableLocalChapter(manga, manga.last_read_chapter);
    const resumeOnline = manga.last_read_online === true || !canReadOffline;
    const progressText = isContinue
        ? `Bölüm ${chapterNum} · Sayfa ${(manga.last_read_page || 0) + 1}`
        : `${downloadedCount} bölüm · ${formatStorageBytes(manga.storage_bytes)}`;

    const card = document.createElement('div');
    card.className = 'manga-card library-manga-card';
    card.dataset.mangaId = id;
    card.onclick = () => isContinue ? openLibraryManga(id, resumeOnline) : openLibraryManga(id, false);
    card.innerHTML = `
        <div class="card-cover-wrapper">
            <img src="${escapeHtml(coverSrc || '/static/img/no-cover.jpg')}" alt="${escapeHtml(manga.title)}" class="card-cover" loading="lazy" decoding="async" fetchpriority="low">
            ${Number(manga.unread_count) > 0 ? `<span class="library-unread-badge" data-manga-id="${escapeHtml(id)}">${Number(manga.unread_count)} yeni</span>` : ''}
            ${Number(manga.mal_id) > 0 ? '<span class="library-mal-badge" title="MyAnimeList ile eşitleniyor" aria-label="MyAnimeList kaydı">MAL</span>' : ''}
            ${isContinue ? `
                <div class="library-card-actions" aria-label="${escapeHtml(manga.title)} okuma işlemleri">
                    <button class="library-card-action library-card-resume" type="button">
                        <i class="fa-solid fa-play"></i><span>Devam Et</span>
                    </button>
                    <button class="library-card-action library-card-chapters" type="button">
                        <i class="fa-solid fa-list-ul"></i><span>Diğer Bölümlere Bak</span>
                    </button>
                </div>
            ` : '<span class="library-card-kind"><i class="fa-solid fa-download"></i> Cihazda</span>'}
            ${libraryRemoveButtonMarkup(manga)}
        </div>
        <div class="card-content">
            <span class="card-status ${escapeHtml(manga.status || 'ongoing')}">${manga.status === 'completed' ? 'Tamamlandı' : 'Devam Ediyor'}</span>
            <h3 class="card-title" title="${escapeHtml(manga.title)}">${escapeHtml(manga.title)}</h3>
            <div class="card-progress"><i class="fa-solid ${isContinue ? 'fa-clock-rotate-left' : 'fa-hard-drive'}"></i><span>${escapeHtml(progressText)}</span></div>
            ${isContinue ? '' : '<div class="library-card-download-state hidden"></div>'}
        </div>`;

    const coverImage = card.querySelector('.card-cover');
    coverImage.addEventListener('error', () => {
        if (coverImage.dataset.remoteFallback !== 'used' && manga.cover_url && coverImage.src !== manga.cover_url) {
            coverImage.dataset.remoteFallback = 'used';
            coverImage.src = manga.cover_url;
        } else {
            coverImage.src = '/static/img/no-cover.jpg';
        }
    });
    card.querySelector('.library-delete-btn').addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        deleteLibraryManga(id, manga);
    });
    card.querySelector('.library-card-resume')?.addEventListener('click', event => {
        event.stopPropagation();
        resumeLibraryManga(id);
    });
    card.querySelector('.library-card-chapters')?.addEventListener('click', event => {
        event.stopPropagation();
        openLibraryManga(id, resumeOnline);
    });
    return card;
}

const libraryStatusLabels = {
    reading: 'Okunuyor',
    completed: 'Tamamlandı',
    on_hold: 'Beklemede',
    dropped: 'Bırakıldı',
    plan_to_read: 'Okuma Planı',
};

function getLibraryStatusLabel(status) {
    return libraryStatusLabels[status] || libraryStatusLabels.reading;
}

function scheduleKnownChapterSync(mangaId, chapters = []) {
    if (!mangaId || !libraryData.mangas[mangaId]) return;
    const pending = pendingKnownChapters.get(mangaId) || new Set();
    chapters.forEach(chapter => {
        const value = String(chapter?.chapter ?? '').trim();
        if (value) pending.add(value);
    });
    if (!pending.size) return;
    pendingKnownChapters.set(mangaId, pending);
    if (knownChapterSyncTimers.has(mangaId)) clearTimeout(knownChapterSyncTimers.get(mangaId));
    knownChapterSyncTimers.set(mangaId, setTimeout(async () => {
        knownChapterSyncTimers.delete(mangaId);
        const chapterNumbers = [...(pendingKnownChapters.get(mangaId) || [])];
        pendingKnownChapters.delete(mangaId);
        try {
            const response = await fetch(`/api/library/${encodeURIComponent(mangaId)}/known-chapters`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chapter_numbers: chapterNumbers }),
            });
            if (!response.ok) return;
            const result = await response.json();
            if (result.manga) {
                libraryData.mangas[mangaId] = result.manga;
                if (activeLibraryView === 'catalog') renderLibraryCatalog();
            }
        } catch (error) {
            console.warn('Okunmamış bölüm bilgisi güncellenemedi:', error);
        }
    }, 450));
}

function formatLibraryUpdatedAt(value) {
    const timestamp = Number(value) || 0;
    if (!timestamp) return 'Henüz güncellenmedi';
    return new Intl.DateTimeFormat('tr-TR', { day: '2-digit', month: 'short', year: 'numeric' })
        .format(new Date(timestamp * 1000));
}

function buildLibraryCatalogCard(id, manga) {
    const card = document.createElement('article');
    const selected = selectedLibraryMangaIds.has(id);
    const collections = Array.isArray(manga.collections) ? manga.collections : [];
    const coverSrc = manga.cover_path
        ? '/' + manga.cover_path.split('/').map(segment => encodeURIComponent(segment)).join('/')
        : manga.cover_url;
    const fullEditionActions = !isReaderEdition() ? `
        <div class="library-catalog-actions" aria-label="${escapeHtml(manga.title)} okuma işlemleri">
            ${manga.last_read_chapter ? `
                <button class="library-card-action library-card-resume library-catalog-resume" type="button">
                    <i class="fa-solid fa-play"></i><span>Devam Et</span>
                </button>
            ` : ''}
            <button class="library-card-action library-card-chapters library-catalog-online" type="button">
                <i class="fa-solid fa-globe"></i><span>Online Bölümlere Bak</span>
            </button>
        </div>
    ` : '';
    card.className = `library-catalog-card${selected ? ' selected' : ''}`;
    card.dataset.mangaId = id;
    card.innerHTML = `
        <button class="library-card-selector" type="button" aria-label="${escapeHtml(manga.title)} seç" aria-pressed="${selected}">
            <i class="fa-solid ${selected ? 'fa-circle-check' : 'fa-circle'}"></i>
        </button>
        <div class="library-catalog-cover">
            <img src="${escapeHtml(coverSrc || '/static/img/no-cover.jpg')}" alt="${escapeHtml(manga.title)}" loading="lazy" decoding="async" fetchpriority="low">
            ${Number(manga.unread_count) > 0 ? `<span class="library-unread-badge" data-manga-id="${escapeHtml(id)}">${Number(manga.unread_count)} yeni</span>` : ''}
            ${Number(manga.mal_id) > 0 ? '<span class="library-mal-badge" title="MyAnimeList ile eşitleniyor" aria-label="MyAnimeList kaydı">MAL</span>' : ''}
            ${fullEditionActions}
            ${libraryRemoveButtonMarkup(manga)}
        </div>
        <div class="library-catalog-copy">
            <div class="library-catalog-heading">
                <span class="library-status-pill ${escapeHtml(manga.library_status || 'reading')}">${getLibraryStatusLabel(manga.library_status)}</span>
                ${Number(manga.user_rating) > 0 ? `<span class="library-rating"><i class="fa-solid fa-star"></i> ${Number(manga.user_rating)}/10</span>` : ''}
                ${manga.tracking_enabled ? `<span class="library-tracking-indicator" title="Yeni bölümler takip ediliyor"><i class="fa-solid fa-satellite-dish"></i></span>` : ''}
            </div>
            <h3 title="${escapeHtml(manga.title)}">${escapeHtml(manga.title)}</h3>
            <p class="library-catalog-progress"><i class="fa-solid fa-clock-rotate-left"></i> ${manga.last_read_chapter_num ? `Bölüm ${escapeHtml(manga.last_read_chapter_num)}` : (Number(manga.mal_num_chapters_read) > 0 ? `MAL · Bölüm ${Number(manga.mal_num_chapters_read)}` : 'Henüz okunmadı')}</p>
            <div class="library-collection-tags">${collections.slice(0, 3).map(value => `<span>${escapeHtml(value)}</span>`).join('')}</div>
            <div class="library-catalog-footer">
                <small><i class="fa-regular fa-calendar"></i> ${formatLibraryUpdatedAt(manga.updated_at || manga.last_read_at)}</small>
                <button class="library-edit-btn" type="button"><i class="fa-solid fa-pen"></i> Düzenle</button>
            </div>
        </div>`;

    const image = card.querySelector('img');
    image.addEventListener('error', () => { image.src = '/static/img/no-cover.jpg'; });
    card.querySelector('.library-catalog-resume')?.addEventListener('click', event => {
        event.stopPropagation();
        resumeLibraryManga(id);
    });
    card.querySelector('.library-catalog-online')?.addEventListener('click', event => {
        event.stopPropagation();
        openLibraryManga(id, true);
    });
    card.querySelector('.library-delete-btn')?.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        deleteLibraryManga(id, manga);
    });
    card.addEventListener('click', event => {
        if (event.target.closest('.library-edit-btn')) {
            openLibraryEditor(id);
            return;
        }
        if (librarySelectionMode || event.target.closest('.library-card-selector')) {
            toggleLibraryMangaSelection(id);
            return;
        }
        openLibraryManga(id, Boolean(manga.last_read_online));
    });
    return card;
}

function refreshLibraryCollectionOptions(entries) {
    const select = document.getElementById('library-collection-filter');
    if (!select) return;
    const collections = [...new Set(entries.flatMap(([, manga]) => manga.collections || []))]
        .sort((a, b) => a.localeCompare(b, 'tr'));
    select.innerHTML = '<option value="">Tüm koleksiyonlar</option>'
        + collections.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    if (collections.includes(activeLibraryCollection)) select.value = activeLibraryCollection;
    else activeLibraryCollection = '';
}

function renderLibraryCatalog() {
    const grid = document.getElementById('library-catalog-grid');
    if (!grid) return;
    const allEntries = Object.entries(libraryData.mangas || {});
    const statusCounts = Object.fromEntries(
        Object.keys(libraryStatusLabels).map(status => [status, 0]),
    );
    allEntries.forEach(([, manga]) => {
        const status = manga.library_status || 'reading';
        if (Object.prototype.hasOwnProperty.call(statusCounts, status)) statusCounts[status] += 1;
    });
    refreshLibraryCollectionOptions(allEntries);
    const entries = allEntries.filter(([, manga]) => {
        const statusMatches = activeLibraryStatus === 'all'
            || (manga.library_status || 'reading') === activeLibraryStatus;
        const collectionMatches = !activeLibraryCollection
            || (manga.collections || []).includes(activeLibraryCollection);
        return statusMatches && collectionMatches;
    });
    entries.sort((a, b) => {
        if (librarySortOrder === 'title_asc') return a[1].title.localeCompare(b[1].title, 'tr');
        if (librarySortOrder === 'rating_desc') return (b[1].user_rating || 0) - (a[1].user_rating || 0) || a[1].title.localeCompare(b[1].title, 'tr');
        if (librarySortOrder === 'unread_desc') return (b[1].unread_count || 0) - (a[1].unread_count || 0) || a[1].title.localeCompare(b[1].title, 'tr');
        return (b[1].updated_at || b[1].last_read_at || 0) - (a[1].updated_at || a[1].last_read_at || 0);
    });
    visibleLibraryMangaIds = entries.map(([id]) => id);
    grid.classList.toggle('compact-list', libraryCatalogView === 'list');
    grid.classList.toggle('selection-mode', librarySelectionMode);
    grid.innerHTML = '';
    entries.forEach(([id, manga]) => grid.appendChild(buildLibraryCatalogCard(id, manga)));
    document.getElementById('library-catalog-empty').style.display = entries.length ? 'none' : 'block';
    document.getElementById('library-catalog-count').textContent = allEntries.length;
    document.getElementById('library-cover-view-btn')?.classList.toggle('active', libraryCatalogView === 'cover');
    document.getElementById('library-list-view-btn')?.classList.toggle('active', libraryCatalogView === 'list');
    document.querySelectorAll('[data-library-status]').forEach(button => {
        button.classList.toggle('active', button.dataset.libraryStatus === activeLibraryStatus);
        const counter = button.querySelector('[data-library-status-count]');
        if (counter) {
            counter.textContent = button.dataset.libraryStatus === 'all'
                ? allEntries.length
                : (statusCounts[button.dataset.libraryStatus] || 0);
        }
    });
    updateLibrarySelectionBar();
}

function setLibraryStatusFilter(status) {
    activeLibraryStatus = Object.prototype.hasOwnProperty.call(libraryStatusLabels, status) ? status : 'all';
    renderLibraryCatalog();
}

function setLibraryCollectionFilter(collection) {
    activeLibraryCollection = collection || '';
    renderLibraryCatalog();
}

function setLibrarySortOrder(order) {
    librarySortOrder = ['updated_desc', 'title_asc', 'rating_desc', 'unread_desc'].includes(order) ? order : 'updated_desc';
    renderLibraryCatalog();
}

function setLibraryCatalogView(view) {
    libraryCatalogView = view === 'list' ? 'list' : 'cover';
    try { localStorage.setItem('mangax-library-view', libraryCatalogView); } catch (_) { /* non-persistent fallback */ }
    renderLibraryCatalog();
}

function toggleLibrarySelectionMode() {
    librarySelectionMode = !librarySelectionMode;
    if (!librarySelectionMode) selectedLibraryMangaIds.clear();
    renderLibraryCatalog();
}

function toggleLibraryMangaSelection(id) {
    if (!librarySelectionMode) librarySelectionMode = true;
    if (selectedLibraryMangaIds.has(id)) selectedLibraryMangaIds.delete(id);
    else selectedLibraryMangaIds.add(id);
    renderLibraryCatalog();
}

function clearLibrarySelection() {
    selectedLibraryMangaIds.clear();
    renderLibraryCatalog();
}

function toggleSelectAllVisibleLibraryMangas() {
    if (!visibleLibraryMangaIds.length) {
        showToast('Bu görünümde seçilebilecek seri yok.', 'info');
        return;
    }
    const allVisibleSelected = visibleLibraryMangaIds.every(id => selectedLibraryMangaIds.has(id));
    visibleLibraryMangaIds.forEach(id => {
        if (allVisibleSelected) selectedLibraryMangaIds.delete(id);
        else selectedLibraryMangaIds.add(id);
    });
    renderLibraryCatalog();
}

function updateLibrarySelectionBar() {
    const bar = document.getElementById('library-bulk-bar');
    const button = document.getElementById('library-select-toggle');
    if (!bar || !button) return;
    bar.classList.toggle('hidden', !librarySelectionMode);
    document.getElementById('library-selected-count').textContent = selectedLibraryMangaIds.size;
    const selectAllButton = document.getElementById('library-select-all');
    const deleteButton = document.getElementById('library-bulk-delete');
    const allVisibleSelected = visibleLibraryMangaIds.length > 0
        && visibleLibraryMangaIds.every(id => selectedLibraryMangaIds.has(id));
    if (selectAllButton) {
        selectAllButton.disabled = visibleLibraryMangaIds.length === 0 || libraryBulkDeleteInFlight;
        selectAllButton.setAttribute('aria-pressed', String(allVisibleSelected));
        selectAllButton.innerHTML = allVisibleSelected
            ? '<i class="fa-solid fa-square-minus" aria-hidden="true"></i> Görünenlerin Seçimini Kaldır'
            : '<i class="fa-solid fa-check-double" aria-hidden="true"></i> Görünenlerin Tümünü Seç';
    }
    if (deleteButton) deleteButton.disabled = selectedLibraryMangaIds.size === 0 || libraryBulkDeleteInFlight;
    button.classList.toggle('active', librarySelectionMode);
    button.innerHTML = librarySelectionMode
        ? '<i class="fa-solid fa-xmark"></i> Seçimi Bitir'
        : '<i class="fa-solid fa-check-double"></i> Toplu Seç';
}

async function deleteSelectedLibraryMangas() {
    if (libraryBulkDeleteInFlight) return;
    const mangaIds = [...selectedLibraryMangaIds].filter(id => libraryData.mangas?.[id]);
    if (!mangaIds.length) return showToast('Önce en az bir seri seçin.', 'error');

    const mangas = mangaIds.map(id => libraryData.mangas[id]);
    const localChapterCount = mangas.reduce(
        (total, manga) => total + Object.keys(manga.downloaded_chapters || {}).length,
        0,
    );
    const localMessage = localChapterCount > 0
        ? ` Cihazdaki ${localChapterCount} yerel bölüm ve bunlara ait yönetilen dosyalar da güvenli biçimde silinecek.`
        : ' Okuma durumları, ilerleme, kişisel notlar ve koleksiyon bağlantıları kaldırılacak.';
    const confirmed = await showAppConfirm({
        title: 'Seçilen Mangaları Kaldır',
        message: `${mangaIds.length} manga kütüphaneden kaldırılacak.${localMessage}`,
        confirmText: `${mangaIds.length} Mangayı Kaldır`,
        variant: 'danger',
        icon: 'fa-trash-can',
    });
    if (!confirmed) return;

    libraryBulkDeleteInFlight = true;
    updateLibrarySelectionBar();
    try {
        const response = await fetch('/api/library/bulk-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ manga_ids: mangaIds }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Seçilen seriler kaldırılamadı.');

        const clearedIds = [...(result.removed_ids || []), ...(result.missing_ids || [])];
        clearedIds.forEach(id => {
            delete libraryData.mangas[id];
            selectedLibraryMangaIds.delete(id);
        });
        (result.failed_ids || []).forEach(id => selectedLibraryMangaIds.add(id));
        if (activeManga && clearedIds.includes(activeManga.id)) closeDetailsModal();
        if (!selectedLibraryMangaIds.size) librarySelectionMode = false;
        renderLibrarySnapshot(libraryData);
        cacheLibrarySnapshot(libraryData);

        if ((result.failed_ids || []).length) {
            showToast(`${result.removed || 0} manga kaldırıldı; ${(result.failed_ids || []).length} manga kaldırılamadı.`, 'error');
        } else {
            showToast(`${result.removed || 0} manga kütüphaneden kaldırıldı.`, 'success');
        }
        loadLibrary({ silent: true });
    } catch (error) {
        showToast(error.message || 'Toplu kaldırma sırasında hata oluştu.', 'error');
    } finally {
        libraryBulkDeleteInFlight = false;
        updateLibrarySelectionBar();
    }
}

async function applyLibraryBulkUpdate() {
    const mangaIds = [...selectedLibraryMangaIds];
    const libraryStatus = document.getElementById('library-bulk-status').value || null;
    const addCollection = document.getElementById('library-bulk-collection').value.trim();
    if (!mangaIds.length) return showToast('Önce en az bir seri seçin.', 'error');
    if (!libraryStatus && !addCollection) return showToast('Bir durum veya koleksiyon seçin.', 'error');
    try {
        const response = await fetch('/api/library/bulk-update', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ manga_ids: mangaIds, library_status: libraryStatus, add_collection: addCollection }),
        });
        if (!response.ok) throw new Error('Toplu düzenleme kaydedilemedi.');
        const result = await response.json();
        (result.mangas || []).forEach(manga => { libraryData.mangas[manga.id] = manga; });
        document.getElementById('library-bulk-collection').value = '';
        document.getElementById('library-bulk-status').value = '';
        selectedLibraryMangaIds.clear();
        renderLibraryCatalog();
        showToast(`${result.updated} seri güncellendi.`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function openLibraryEditor(id) {
    const manga = libraryData.mangas[id];
    if (!manga) return;
    activeLibraryEditorId = id;
    document.getElementById('library-editor-title').textContent = manga.title;
    document.getElementById('library-editor-status').value = manga.library_status || 'reading';
    document.getElementById('library-editor-rating').value = Number(manga.user_rating) || 0;
    document.getElementById('library-editor-rating-value').textContent = `${Number(manga.user_rating) || 0} / 10`;
    const malProgress = document.getElementById('library-mal-progress-fields');
    malProgress?.classList.toggle('hidden', !(Number(manga.mal_id) > 0));
    document.getElementById('library-editor-mal-chapters').value = Number(manga.mal_num_chapters_read) || 0;
    document.getElementById('library-editor-mal-volumes').value = Number(manga.mal_num_volumes_read) || 0;
    document.getElementById('library-editor-collections').value = (manga.collections || []).join(', ');
    document.getElementById('library-editor-note').value = manga.personal_note || '';
    document.getElementById('library-editor-tracking').checked = Boolean(manga.tracking_enabled);
    document.getElementById('library-editor-tracking-notifications').checked = manga.tracking_notifications !== false;
    document.getElementById('library-editor-tracking-download').checked = Boolean(manga.tracking_auto_download);
    const trackingSource = document.getElementById('library-editor-tracking-source');
    if (trackingSource) {
        trackingSource.classList.toggle('error', Boolean(manga.tracking_last_error));
        trackingSource.textContent = manga.tracking_last_error
            ? `Son kontrol: ${manga.tracking_last_error}`
            : manga.tracking_source_name
                ? `Takip kaynağı: ${manga.tracking_source_name}`
                : 'Takip kaynağı ilk kontrolde otomatik belirlenecek.';
    }
    syncLibraryTrackingEditorState();
    const editor = document.getElementById('library-editor');
    editor.classList.add('active');
    editor.setAttribute('aria-hidden', 'false');
}

function closeLibraryEditor() {
    activeLibraryEditorId = '';
    const editor = document.getElementById('library-editor');
    editor?.classList.remove('active');
    editor?.setAttribute('aria-hidden', 'true');
}

function syncLibraryTrackingEditorState() {
    const enabled = Boolean(document.getElementById('library-editor-tracking')?.checked);
    const notifications = document.getElementById('library-editor-tracking-notifications');
    const download = document.getElementById('library-editor-tracking-download');
    if (notifications) notifications.disabled = !enabled;
    if (download) download.disabled = !enabled;
}

async function saveLibraryMetadata(event) {
    event.preventDefault();
    if (!activeLibraryEditorId) return;
    const saveButton = document.getElementById('library-editor-save');
    saveButton.disabled = true;
    try {
        const response = await fetch(`/api/library/${encodeURIComponent(activeLibraryEditorId)}/metadata`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                library_status: document.getElementById('library-editor-status').value,
                user_rating: Number(document.getElementById('library-editor-rating').value),
                personal_note: document.getElementById('library-editor-note').value,
                collections: document.getElementById('library-editor-collections').value.split(',').map(value => value.trim()).filter(Boolean),
            }),
        });
        if (!response.ok) throw new Error('Kitaplık bilgileri kaydedilemedi.');
        const result = await response.json();
        libraryData.mangas[activeLibraryEditorId] = result.manga;
        closeLibraryEditor();
        renderLibraryCatalog();
        showToast('Kitaplık bilgileri kaydedildi.', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        saveButton.disabled = false;
    }
}

async function openLibraryManga(id) {
    detailOnlineMode = false;
    await viewMangaDetails(id);
}

async function deleteLibraryManga(mangaId, manga = {}) {
    if (!mangaId || deletingLibraryMangaIds.has(mangaId)) return;
    deletingLibraryMangaIds.add(mangaId);
    setLibraryRemoveBusy(mangaId, true);

    const mangaTitle = String(manga.title || libraryData.mangas?.[mangaId]?.title || 'Bu manga');
    const localChapterCount = Object.keys(manga.downloaded_chapters || {}).length;
    const localDataCopy = localChapterCount > 0
        ? ` Kütüphane kaydıyla birlikte cihazdaki ${localChapterCount} yerel bölüm ve bu mangaya ait yönetilen dosyalar da güvenli biçimde silinecek.`
        : ' Kütüphane durumu, okuma ilerlemesi, kişisel notu ve koleksiyon bağlantıları kaldırılacak.';

    try {
        const confirmed = await showAppConfirm({
            title: 'Mangayı Kütüphaneden Kaldır',
            message: `“${mangaTitle}” kütüphaneden kaldırılacak.${localDataCopy}`,
            confirmText: 'Kaldır',
            variant: 'danger',
            icon: 'fa-trash-can'
        });
        if (!confirmed) return;

        const response = await fetch(`/api/library/${encodeURIComponent(mangaId)}`, { method: 'DELETE' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Seri silinemedi.');

        delete libraryData.mangas[mangaId];
        selectedLibraryMangaIds.delete(mangaId);
        renderLibrarySnapshot(libraryData);
        cacheLibrarySnapshot(libraryData);
        if (activeManga && activeManga.id === mangaId) closeDetailsModal();
        showToast(`“${mangaTitle}” kütüphaneden kaldırıldı.`, 'success');
        loadLibrary({ silent: true });
    } catch (error) {
        showToast(error.message || 'Manga kütüphaneden kaldırılırken hata oluştu.', 'error');
    } finally {
        deletingLibraryMangaIds.delete(mangaId);
        setLibraryRemoveBusy(mangaId, false);
    }
}

async function resumeLibraryManga(id) {
    const manga = libraryData.mangas[id];
    if (!manga?.last_read_chapter) return;
    const downloaded = manga.downloaded_chapters || {};
    if (!Object.prototype.hasOwnProperty.call(downloaded, manga.last_read_chapter)) return;
    detailOnlineMode = false;
    prepareReaderForResume(manga.title || 'Manga', manga.last_read_chapter_num || '?');
    activeManga = manga;
    activeChapters = Object.values(downloaded).map(chapter => ({
        id: chapter.id,
        chapter: chapter.chapter,
        title: chapter.title,
        language: chapter.language || 'tr',
        group: chapter.group || 'Yerel',
    }));
    await startReading(id, manga.last_read_chapter, false);
}

function renderLibrarySnapshot(data) {
    configureLibraryEditionLayout();
    const previousMangas = libraryData?.mangas || {};
    const incomingMangas = data?.mangas || {};
    libraryData = {
        mangas: Object.fromEntries(Object.entries(incomingMangas).map(([id, manga]) => [
            id,
            manga.storage_bytes === undefined && previousMangas[id]?.storage_bytes !== undefined
                ? { ...manga, storage_bytes: previousMangas[id].storage_bytes }
                : manga,
        ])),
    };
    const entries = Object.entries(libraryData.mangas || {}).filter(([, manga]) => (
        !isReaderEdition() || Object.keys(manga.downloaded_chapters || {}).length > 0
    ));
    const continuing = entries
        .filter(([, manga]) => Boolean(manga.last_read_chapter))
        .sort((a, b) => (b[1].last_read_at || 0) - (a[1].last_read_at || 0));
    const downloaded = entries
        .filter(([, manga]) => Object.keys(manga.downloaded_chapters || {}).length > 0)
        .sort((a, b) => (b[1].last_read_at || 0) - (a[1].last_read_at || 0));

    const continueGrid = document.getElementById('library-continue-grid');
    const downloadedGrid = document.getElementById('library-downloaded-grid');
    if (continueGrid) {
        continueGrid.innerHTML = '';
        continuing.forEach(([id, manga]) => continueGrid.appendChild(buildLibraryCard(id, manga, 'continue')));
    }
    if (downloadedGrid) {
        downloadedGrid.innerHTML = '';
        downloaded.forEach(([id, manga]) => downloadedGrid.appendChild(buildLibraryCard(id, manga, 'downloaded')));
    }
    const continueEmpty = document.getElementById('library-continue-empty');
    const downloadedEmpty = document.getElementById('library-downloaded-empty');
    const continueCount = document.getElementById('library-continue-count');
    const downloadedCount = document.getElementById('library-downloaded-count');
    if (continueEmpty) continueEmpty.style.display = continuing.length ? 'none' : 'block';
    if (downloadedEmpty) downloadedEmpty.style.display = downloaded.length ? 'none' : 'block';
    if (continueCount) continueCount.textContent = continuing.length;
    if (downloadedCount) downloadedCount.textContent = downloaded.length;
    renderLibraryCatalog();
    switchLibraryView(activeLibraryView);
    updateLibraryCardDownloadStates();
}

async function loadLibrary({ silent = false } = {}) {
    const initialRequest = !initialLibraryRequestCompleted;
    if (initialRequest && typeof markStartupMilestone === 'function') {
        markStartupMilestone('library_request_started');
    }
    try {
        const response = await fetch('/api/library', { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const freshLibrary = await response.json();
        renderLibrarySnapshot(freshLibrary);
        cacheLibrarySnapshot(libraryData);
        if (initialRequest && typeof markStartupMilestone === 'function') {
            markStartupMilestone('library_rendered');
        }
    } catch (e) {
        console.error('Library failed to load', e);
        if (!silent) showToast('Kütüphane yüklenirken hata oluştu.', 'error');
    } finally {
        if (initialRequest) initialLibraryRequestCompleted = true;
    }
}
