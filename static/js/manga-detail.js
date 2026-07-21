// View Manga Details Modal
const MANGA_DETAIL_CACHE_TTL_MS = 3 * 60 * 1000;
const mangaDetailResponseCache = new Map();
let detailRequestController = null;

async function fetchMangaDetailJson(url, { signal, ttl = MANGA_DETAIL_CACHE_TTL_MS } = {}) {
    const cached = mangaDetailResponseCache.get(url);
    if (cached && Date.now() - cached.savedAt < ttl) return cached.data;
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    mangaDetailResponseCache.set(url, { savedAt: Date.now(), data });
    if (mangaDetailResponseCache.size > 60) mangaDetailResponseCache.delete(mangaDetailResponseCache.keys().next().value);
    return data;
}

function renderMangaDetailTags(tags = []) {
    const container = document.getElementById('manga-detail-tags');
    if (!container) return;
    container.innerHTML = '';
    [...new Set(Array.isArray(tags) ? tags : [])].slice(0, 5).forEach(tag => {
        const discoverAvailable = typeof isGithubExtensionsAvailable === 'function' && isGithubExtensionsAvailable();
        const element = document.createElement(discoverAvailable ? 'button' : 'span');
        if (discoverAvailable) {
            element.type = 'button';
            element.className = 'tag-badge tag-filter-button';
            element.title = `${tag} türündeki diğer mangaları keşfet`;
            element.setAttribute('aria-label', `${tag} türündeki mangaları göster`);
            element.addEventListener('click', () => openDiscoverGenre(tag));
        } else {
            element.className = 'tag-badge';
        }
        element.textContent = tag;
        container.appendChild(element);
    });
}

function handleChapterSearch() {
    chapterSearchQuery = document.getElementById('chapter-search-input').value.trim();
    renderChapters();
}

function toggleChapterSort() {
    const btnText = document.getElementById('sort-btn-text');
    const btnIcon = document.querySelector('#chapter-sort-btn i');
    chapterSortOrder = chapterSortOrder === 'asc' ? 'desc' : 'asc';
    btnText.textContent = chapterSortOrder === 'asc' ? 'Eskiden Yeniye' : 'Yeniden Eskiye';
    btnIcon.className = chapterSortOrder === 'asc'
        ? 'fa-solid fa-sort-numeric-down'
        : 'fa-solid fa-sort-numeric-up';
    renderChapters();
}

function buildReaderOnlyChapterActions(chapter, downloadedChapters) {
    const isLocal = Object.prototype.hasOwnProperty.call(downloadedChapters, chapter.id);
    const { element, icon, text } = window.MangaXSafeDOM;
    const button = element('button', { className: 'btn btn-secondary btn-sm', type: 'button' }, [icon('fa-solid fa-book-open'), text(' Oku')]);
    button.addEventListener('click', () => startReading(activeManga.id, chapter.id, !isLocal));
    return element('div', { className: 'chapters-actions' }, [button]);
}

async function viewMangaDetails(mangaId, options = {}) {
    const { showModal = true } = options;
    detailRequestController?.abort();
    const requestController = new AbortController();
    detailRequestController = requestController;
    const { signal } = requestController;
    const requestId = ++detailRequestCounter; // Track this request sequence
    
    const modal = document.getElementById('details-modal');
    const loader = document.getElementById('chapters-loader');
    const listBody = document.getElementById('chapters-list-body');
    const selectorWrapper = document.getElementById('manga-source-selector-wrapper');
    const selector = document.getElementById('manga-source-select');
    const sourcesLoadingStatus = document.getElementById('manga-sources-loading-status');
    const onlineWrapper = document.getElementById('online-mode-wrapper');
    const onlineToggle = document.getElementById('manga-online-toggle');
    const sourceBindingPanel = document.getElementById('source-binding-panel');
    
    // Reset view
    listBody.innerHTML = "";
    allFetchedSources = [];
    sourceBindingPanel?.classList.add('hidden');
    loader.classList.remove('hidden');
    if (showModal) modal.classList.add('active');
    
    if (selectorWrapper) {
        selectorWrapper.classList.add('hidden');
    }
    if (sourcesLoadingStatus) {
        sourcesLoadingStatus.classList.add('hidden');
        sourcesLoadingStatus.classList.remove('complete');
        const statusIcon = sourcesLoadingStatus.querySelector('i');
        if (statusIcon) statusIcon.className = 'fa-solid fa-spinner fa-spin';
    }
    
    // Reset chapter search and sort states
    chapterSearchQuery = "";
    chapterSortOrder = "asc";
    activeGroup = "";
    const searchInput = document.getElementById('chapter-search-input');
    if (searchInput) searchInput.value = "";
    const groupSelect = document.getElementById('chapter-group-select');
    if (groupSelect) groupSelect.innerHTML = '<option value="">Tüm Gruplar</option>';
    const sortBtnText = document.getElementById('sort-btn-text');
    if (sortBtnText) sortBtnText.textContent = "Eskiden Yeniye";
    const sortBtnIcon = document.querySelector('#chapter-sort-btn i');
    if (sortBtnIcon) sortBtnIcon.className = "fa-solid fa-sort-numeric-down";
    
    const isMangaInLibrary = libraryData.mangas[mangaId] !== undefined;
    
    // ── ÇEVRİMDIŞI (YEREL) ÖNCELİKLİ MOD ───────────────────────────────────────
    if (isMangaInLibrary && !detailOnlineMode) {
        // Çevrimiçi Gör toggle switch'ini göster ve kapalı tut
        if (onlineWrapper) onlineWrapper.classList.remove('hidden');
        if (onlineToggle) onlineToggle.checked = false;
        
        loader.classList.add('hidden');
        const localManga = libraryData.mangas[mangaId];
        activeManga = localManga;
        const searchManga = searchResults[mangaId] || null;
        
        // Yerel indirilen bölümleri yükle
        activeChapters = Object.values(localManga.downloaded_chapters || {}).map(ch => ({
            id: ch.id,
            chapter: ch.chapter,
            title: ch.title,
            language: ch.language || 'tr',
            group: ch.group || 'İndirilenler'
        }));
        
        // Detay bilgilerini çiz (eksik veri varsa arama önbelleğinden tamamla)
        const displayTitle = localManga.title || (searchManga ? searchManga.title : "Bilinmeyen Başlık");
        const displayDesc = localManga.description || (searchManga ? searchManga.description : "Açıklama bulunmuyor.");
        const displayYear = localManga.year || (searchManga ? searchManga.year : "");
        const displayStatus = localManga.status || (searchManga ? searchManga.status : "ongoing");
        const displayTags = (localManga.tags && localManga.tags.length > 0) ? localManga.tags : (searchManga ? searchManga.tags : []);
        
        document.getElementById('manga-detail-title').textContent = displayTitle;
        document.getElementById('manga-detail-desc').textContent = displayDesc;
        
        // Kapak resmi yerel yoldan, yoksa uzak adresten, o da yoksa arama sonucundan alınır
        let coverSrc = localManga.cover_local_url || (localManga.cover_path
            ? '/' + localManga.cover_path.split('/').map(s => encodeURIComponent(s)).join('/')
            : (localManga.cover_url || (searchManga ? searchManga.cover_url : '')));
        window.MangaXSafeDOM.setImageSource(document.getElementById('manga-detail-cover'), coverSrc);
        
        const bannerImg = localManga.banner_url || (searchManga ? searchManga.banner_url : '') || coverSrc || '';
        window.MangaXSafeDOM.setBackgroundImage(document.getElementById('manga-detail-banner'), bannerImg);
        document.getElementById('manga-detail-year').textContent = displayYear;
        
        const statusBadge = document.getElementById('manga-detail-status');
        if (statusBadge) {
            statusBadge.textContent = displayStatus === 'completed' ? 'Tamamlandı' : 'Devam Ediyor';
            statusBadge.className = `manga-status-badge ${displayStatus}`;
        }
        
        renderMangaDetailTags(displayTags);
        
        const hasTr = activeChapters.some(c => c.language === 'tr');
        activeLang = hasTr ? 'tr' : 'en';
        updateLangTabs(hasTr);
        
        const chaptersForLang = activeChapters.filter(c => c.language === activeLang);
        populateGroupSelector(chaptersForLang);
        
        renderChapters();
        return;
    }
    
    // Çevrimiçi Mod toggle görünümünü yönet
    if (onlineWrapper) {
        if (isMangaInLibrary) {
            onlineWrapper.classList.remove('hidden');
            if (onlineToggle) onlineToggle.checked = true;
        } else {
            onlineWrapper.classList.add('hidden');
        }
    }
    
    try {
        // Fetch Details & Chapters
        let searchManga = searchResults[mangaId] || null;
        
        // Immediately draw the new manga metadata to prevent old content flicker and blank layouts!
        if (searchManga) {
            document.getElementById('manga-detail-title').textContent = searchManga.title || "Bilinmeyen Başlık";
            document.getElementById('manga-detail-desc').textContent = searchManga.description || "Açıklama bulunmuyor.";
            window.MangaXSafeDOM.setImageSource(document.getElementById('manga-detail-cover'), searchManga.cover_url);
            
            const bannerImg = searchManga.banner_url || searchManga.cover_url || '';
            window.MangaXSafeDOM.setBackgroundImage(document.getElementById('manga-detail-banner'), bannerImg);
            document.getElementById('manga-detail-year').textContent = searchManga.year || "";
            
            const statusBadge = document.getElementById('manga-detail-status');
            if (statusBadge) {
                statusBadge.textContent = searchManga.status === 'completed' ? 'Tamamlandı' : 'Devam Ediyor';
                statusBadge.className = `manga-status-badge ${searchManga.status || 'ongoing'}`;
            }
            
            renderMangaDetailTags(searchManga.tags);
        } else {
            // Clear metadata fields if no searchManga object
            document.getElementById('manga-detail-title').textContent = "";
            document.getElementById('manga-detail-desc').textContent = "";
            document.getElementById('manga-detail-cover').src = '/static/img/no-cover.jpg';
            document.getElementById('manga-detail-banner').style.backgroundImage = "";
            document.getElementById('manga-detail-year').textContent = "";
            document.getElementById('manga-detail-tags').innerHTML = "";
        }
        
        // AniList kaynaklarını sonuç geldikçe aşamalı yükle.
        if (
            mangaId.startsWith("anilist_")
            || (
                mangaId.startsWith("mal_")
                && window.mangaXFullSourceBindings === true
            )
        ) {
            await loadAniListSourcesProgressively(
                mangaId,
                searchManga,
                requestId,
                loader,
                listBody,
                selectorWrapper,
                selector,
                signal
            );
            return;
        }
        
        // Resolve sources: if grouped, we have _sources list. If not, construct a single source item.
        const sources = (searchManga && searchManga._sources) 
            ? searchManga._sources 
            : (mangaId.startsWith("anilist_") ? [] : [{ id: mangaId, title: searchManga ? searchManga.title : "", source: getSourceLabel(mangaId) }]);
        
        // If no sources resolved, draw metadata and exit
        if (sources.length === 0) {
            listBody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-secondary);">Eşleşen aktif Türkçe/İngilizce kaynak bulunamadı.</td></tr>`;
            loader.classList.add('hidden');
            
            activeManga = searchManga || { title: "Bilinmeyen Manga", tags: [] };
            activeChapters = [];
            
            document.getElementById('manga-detail-title').textContent = activeManga.title;
            document.getElementById('manga-detail-desc').textContent = activeManga.description || "Açıklama bulunmuyor.";
            window.MangaXSafeDOM.setImageSource(document.getElementById('manga-detail-cover'), activeManga.cover_url);
            
            const bannerImg = activeManga.banner_url || activeManga.cover_url || '';
            window.MangaXSafeDOM.setBackgroundImage(document.getElementById('manga-detail-banner'), bannerImg);
            document.getElementById('manga-detail-year').textContent = activeManga.year || "";
            
            const statusBadge = document.getElementById('manga-detail-status');
            statusBadge.textContent = activeManga.status === 'ongoing' ? 'Devam Ediyor' : 'Tamamlandı';
            statusBadge.className = `manga-status-badge ${activeManga.status}`;
            
            renderMangaDetailTags(activeManga.tags);
            
            updateLangTabs(false);
            return;
        }
        
        // Prefetch details and chapters for all sources in parallel
        const fetchPromises = sources.map(async (src) => {
            try {
                const [details, chapters] = await Promise.all([
                    fetchMangaDetailJson(`/api/manga/${src.id}`, { signal }),
                    fetchMangaDetailJson(buildChapterApiUrl(src.id, searchManga), { signal }),
                ]);
                
                return {
                    id: src.id,
                    sourceName: src.source || getSourceLabel(src.id),
                    details: details,
                    chapters: chapters,
                    count: chapters.length
                };
            } catch (e) {
                if (e?.name === 'AbortError') return null;
                console.error(`Failed to prefetch source ${src.id}:`, e);
                return null;
            }
        });
        
        const fetchedSourcesRaw = await Promise.all(fetchPromises);
        if (requestId !== detailRequestCounter) return; // Stale request, discard!
        
        allFetchedSources = fetchedSourcesRaw.filter(x => x !== null);
        scheduleKnownChapterSync(mangaId, allFetchedSources.flatMap(source => source.chapters || []));
        
        if (allFetchedSources.length === 0) {
            showToast("Manga detayları yüklenemedi.", "error");
            closeDetailsModal();
            return;
        }
        
        // Türkçe ve İngilizce kaynakları ayır ve kendi diline göre sırala
        const trSources = allFetchedSources.filter(src => src.chapters.some(c => c.language === 'tr'));
        const enSources = allFetchedSources.filter(src => src.chapters.some(c => c.language === 'en'));
        
        trSources.forEach(src => {
            src.trCount = src.chapters.filter(c => c.language === 'tr').length;
        });
        trSources.sort((a, b) => b.trCount - a.trCount);
        
        enSources.forEach(src => {
            src.enCount = src.chapters.filter(c => c.language === 'en').length;
        });
        enSources.sort((a, b) => b.enCount - a.enCount);
        
        // Show source selector if multiple sources
        if (allFetchedSources.length > 1 && selectorWrapper && selector) {
            selector.innerHTML = "";
            
            if (trSources.length > 0) {
                const trGroup = document.createElement('optgroup');
                trGroup.label = "Türkçe Kaynaklar";
                trSources.forEach((src, idx) => {
                    const opt = document.createElement('option');
                    opt.value = src.id;
                    opt.textContent = `${src.sourceName} (${src.trCount} Bölüm${idx === 0 ? ' - Önerilen' : ''})`;
                    trGroup.appendChild(opt);
                });
                selector.appendChild(trGroup);
            }
            
            if (enSources.length > 0) {
                const enGroup = document.createElement('optgroup');
                enGroup.label = "İngilizce Kaynaklar";
                enSources.forEach((src, idx) => {
                    const opt = document.createElement('option');
                    opt.value = src.id;
                    opt.textContent = `${src.sourceName} (${src.enCount} Bölüm${idx === 0 ? ' - Önerilen' : ''})`;
                    enGroup.appendChild(opt);
                });
                selector.appendChild(enGroup);
            }
            
            selectorWrapper.classList.remove('hidden');
        }
        
        // Select the default source matching the active discover tab language
        let defaultSource = null;
        if (activeDiscoverLang === 'tr') {
            if (trSources.length > 0) {
                defaultSource = trSources[0];
                activeLang = 'tr';
            } else if (enSources.length > 0) {
                defaultSource = enSources[0];
                activeLang = 'en';
            } else {
                defaultSource = allFetchedSources[0];
                activeLang = defaultSource.chapters.some(c => c.language === 'tr') ? 'tr' : 'en';
            }
        } else {
            if (enSources.length > 0) {
                defaultSource = enSources[0];
                activeLang = 'en';
            } else if (trSources.length > 0) {
                defaultSource = trSources[0];
                activeLang = 'tr';
            } else {
                defaultSource = allFetchedSources[0];
                activeLang = defaultSource.chapters.some(c => c.language === 'tr') ? 'tr' : 'en';
            }
        }
        
        if (mangaId.startsWith("anilist_")) {
            activeManga = searchManga;
        } else {
            activeManga = defaultSource.details;
        }
        activeChapters = defaultSource.chapters;
        
        // Render details
        document.getElementById('manga-detail-title').textContent = activeManga.title;
        document.getElementById('manga-detail-desc').textContent = activeManga.description || "Açıklama bulunmuyor.";
        window.MangaXSafeDOM.setImageSource(document.getElementById('manga-detail-cover'), activeManga.cover_url);
        window.MangaXSafeDOM.setBackgroundImage(document.getElementById('manga-detail-banner'), activeManga.banner_url || activeManga.cover_url);
        document.getElementById('manga-detail-year').textContent = activeManga.year || "";
        
        const statusBadge = document.getElementById('manga-detail-status');
        statusBadge.textContent = activeManga.status === 'ongoing' ? 'Devam Ediyor' : 'Tamamlandı';
        statusBadge.className = `manga-status-badge ${activeManga.status}`;
        
        renderMangaDetailTags(activeManga.tags);
        
        // Determine available language priorities
        const hasTr = activeChapters.some(c => c.language === 'tr');
        activeLang = hasTr ? 'tr' : 'en';
        
        // Update language tabs
        updateLangTabs(hasTr);
        
        // Populate group selector with active language chapters
        const chaptersForLang = activeChapters.filter(c => c.language === activeLang);
        populateGroupSelector(chaptersForLang);
        
        loader.classList.add('hidden');
        renderChapters();
    } catch (e) {
        if (e?.name === 'AbortError') return;
        if (requestId === detailRequestCounter) {
            loader.classList.add('hidden');
            console.error(e);
            showToast("Manga detayları yüklenemedi.", "error");
        }
    }
}

function updateProgressiveSourceSelector(selectorWrapper, selector) {
    if (!selectorWrapper || !selector || allFetchedSources.length === 0) return;

    const selectedId = selector.value;
    const trSources = allFetchedSources
        .filter(src => src.chapters.some(chapter => chapter.language === 'tr'))
        .map(src => ({
            ...src,
            trCount: src.chapters.filter(chapter => chapter.language === 'tr').length
        }))
        .sort((a, b) => b.trCount - a.trCount);
    const enSources = allFetchedSources
        .filter(src => src.chapters.some(chapter => chapter.language === 'en'))
        .map(src => ({
            ...src,
            enCount: src.chapters.filter(chapter => chapter.language === 'en').length
        }))
        .sort((a, b) => b.enCount - a.enCount);

    selector.innerHTML = '';
    const appendGroup = (label, sources, language) => {
        if (sources.length === 0) return;
        const group = document.createElement('optgroup');
        group.label = label;
        sources.forEach((source, index) => {
            const option = document.createElement('option');
            const count = language === 'tr' ? source.trCount : source.enCount;
            option.value = source.id;
            option.textContent = `${source.sourceName} (${count} Bölüm${index === 0 ? ' - Önerilen' : ''})`;
            group.appendChild(option);
        });
        selector.appendChild(group);
    };

    appendGroup('Türkçe Kaynaklar', trSources, 'tr');
    appendGroup('İngilizce Kaynaklar', enSources, 'en');

    selectorWrapper.classList.remove('hidden');
    if (selectedId && allFetchedSources.some(source => source.id === selectedId)) {
        selector.value = selectedId;
    } else if (activeChapters.length > 0) {
        const activeSource = allFetchedSources.find(
            source => source.chapters === activeChapters
        );
        if (activeSource) selector.value = activeSource.id;
    }
}

function activateProgressiveSource(source, manga, loader, selector) {
    activeManga = manga;
    activeChapters = source.chapters;

    const hasTr = activeChapters.some(chapter => chapter.language === 'tr');
    const hasEn = activeChapters.some(chapter => chapter.language === 'en');
    if (activeDiscoverLang === 'tr' && hasTr) {
        activeLang = 'tr';
    } else if (activeDiscoverLang === 'en' && hasEn) {
        activeLang = 'en';
    } else {
        activeLang = hasTr ? 'tr' : 'en';
    }

    updateLangTabs(hasTr);
    populateGroupSelector(
        activeChapters.filter(chapter => chapter.language === activeLang)
    );
    if (selector) selector.value = source.id;
    loader.classList.add('hidden');
    renderChapters();
}

function applyProgressiveMetadata(mangaId, searchManga, metadata) {
    const manga = searchManga || { id: mangaId, _sources: [] };
    const fields = [
        'title', 'cover_url', 'banner_url', 'description',
        'status', 'tags', 'year', '_anilist_chapters'
    ];
    fields.forEach(field => {
        if (metadata[field] !== undefined && metadata[field] !== null) {
            manga[field] = metadata[field];
        }
    });
    manga._sources = manga._sources || [];
    searchResults[mangaId] = manga;

    document.getElementById('manga-detail-title').textContent = manga.title || 'Bilinmeyen Başlık';
    document.getElementById('manga-detail-desc').textContent = manga.description || 'Açıklama bulunmuyor.';
    window.MangaXSafeDOM.setImageSource(document.getElementById('manga-detail-cover'), manga.cover_url);
    const banner = manga.banner_url || manga.cover_url || '';
    window.MangaXSafeDOM.setBackgroundImage(document.getElementById('manga-detail-banner'), banner);
    document.getElementById('manga-detail-year').textContent = manga.year || '';

    const statusBadge = document.getElementById('manga-detail-status');
    statusBadge.textContent = manga.status === 'completed' ? 'Tamamlandı' : 'Devam Ediyor';
    statusBadge.className = `manga-status-badge ${manga.status || 'ongoing'}`;

    renderMangaDetailTags(manga.tags);
    return manga;
}

function buildChapterApiUrl(sourceId, manga) {
    const baseUrl = `/api/manga/${encodeURIComponent(sourceId)}/chapters`;
    const expected = Number(manga && manga._anilist_chapters);
    if (!Number.isInteger(expected) || expected <= 0) return baseUrl;
    return `${baseUrl}?anilist_chapters=${expected}`;
}

async function loadAniListSourcesProgressively(
    mangaId,
    initialManga,
    requestId,
    loader,
    listBody,
    selectorWrapper,
    selector,
    signal
) {
    const status = document.getElementById('manga-sources-loading-status');
    const statusText = status ? status.querySelector('span') : null;
    if (status) {
        status.classList.remove('hidden', 'complete', 'warning');
        const statusIcon = status.querySelector('i');
        if (statusIcon) statusIcon.className = 'fa-solid fa-spinner fa-spin';
        if (statusText) statusText.textContent = 'Kaynaklar yükleniyor…';
    }
    listBody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-secondary);"><i class="fa-solid fa-spinner fa-spin" style="margin-right:8px;"></i> İlk kullanılabilir kaynak aranıyor…</td></tr>`;

    allFetchedSources = [];
    activeChapters = [];
    let manga = initialManga || { id: mangaId, _sources: [] };
    const alreadyKnownSources = !mangaId.startsWith('anilist_') && Array.isArray(manga._sources)
        ? manga._sources.filter(source => source.id && !source.id.startsWith('anilist_'))
        : [];
    manga._sources = [];
    searchResults[mangaId] = manga;
    const pendingSourceFetches = new Set();
    let firstSourceShown = false;
    let plannedSources = [];
    const failedSourceNames = [];

    const sourceNamesText = names => {
        const values = [...new Set(names.filter(Boolean))];
        if (values.length <= 1) return values[0] || '';
        return `${values.slice(0, -1).join(', ')} ve ${values.at(-1)}`;
    };

    const showSourceResolutionEmpty = () => {
        const tried = sourceNamesText(plannedSources.map(item => item.name));
        const title = tried
            ? `${tried} denendi ancak bölümü olan bir eşleşme bulunamadı.`
            : 'Denenecek etkin manga kaynağı yok.';
        const detail = tried
            ? 'Kaynaklar çalışmaya devam ediyor olabilir; Eklentiler bölümünden durumlarını test edebilirsin.'
            : 'Eklentiler bölümünden en az bir kaynak kurup yeniden deneyebilirsin.';
        const { element, icon } = window.MangaXSafeDOM;
        const cell = element('td', { attributes: { colspan: '4' } }, [
            element('div', { className: 'source-resolution-message' }, [
                icon('fa-solid fa-circle-info'),
                element('div', {}, [element('strong', { text: title }), element('small', { text: detail })]),
            ]),
        ]);
        listBody.replaceChildren(element('tr', {}, [cell]));
    };

    const loadResolvedSource = resolvedSource => {
        if (!resolvedSource || manga._sources.some(source => source.id === resolvedSource.id)) {
            return;
        }
        manga._sources.push(resolvedSource);

        const sourcePromise = (async () => {
            try {
                const chaptersResponse = await fetch(
                    buildChapterApiUrl(resolvedSource.id, manga),
                    { signal }
                );
                const sourceName = resolvedSource.source || getSourceLabel(resolvedSource.id);
                if (!chaptersResponse.ok) {
                    throw new Error(`${sourceName} bölüm listesini gönderemedi (HTTP ${chaptersResponse.status}).`);
                }
                const chapters = await chaptersResponse.json();
                if (
                    requestId !== detailRequestCounter
                    || !Array.isArray(chapters)
                    || chapters.length === 0
                ) {
                    if (requestId === detailRequestCounter && statusText) {
                        const alternatives = plannedSources
                            .filter(item => item.id !== resolvedSource.id)
                            .map(item => item.name);
                        const nextNames = sourceNamesText(alternatives.slice(0, 3));
                        statusText.textContent = `${sourceName} eşleşti ancak bölüm listesi boş geldi.${nextNames ? ` ${nextNames} deneniyor…` : ''}`;
                    }
                    return;
                }

                const fetchedSource = {
                    id: resolvedSource.id,
                    sourceName: resolvedSource.source || getSourceLabel(resolvedSource.id),
                    details: manga,
                    chapters,
                    count: chapters.length
                };
                allFetchedSources.push(fetchedSource);
                scheduleKnownChapterSync(mangaId, chapters);
                updateProgressiveSourceSelector(selectorWrapper, selector);

                if (!firstSourceShown) {
                    firstSourceShown = true;
                    activateProgressiveSource(fetchedSource, manga, loader, selector);
                }

                if (statusText) {
                    statusText.textContent = `${allFetchedSources.length} kaynak hazır, diğer kaynaklar yükleniyor…`;
                }
            } catch (error) {
                if (error?.name === 'AbortError') return;
                console.error(`Kaynak bölümleri yüklenemedi: ${resolvedSource.id}`, error);
                if (requestId === detailRequestCounter && statusText) {
                    const sourceName = resolvedSource.source || getSourceLabel(resolvedSource.id);
                    const alternatives = plannedSources
                        .filter(item => item.id !== resolvedSource.id)
                        .map(item => item.name);
                    const nextNames = sourceNamesText(alternatives.slice(0, 3));
                    statusText.textContent = `${sourceName} bölüm listesini veremedi.${nextNames ? ` ${nextNames} deneniyor…` : ' Diğer kaynakların kontrolü tamamlandı.'}`;
                }
            }
        })();
        pendingSourceFetches.add(sourcePromise);
        sourcePromise.finally(() => pendingSourceFetches.delete(sourcePromise));
    };

    // Arama kartında daha önce çözülmüş bir kaynak varsa stream'i bile beklemeden dene.
    alreadyKnownSources.forEach(loadResolvedSource);

    try {
        const response = await fetch(`/api/manga/resolve-stream/${mangaId}`, { signal });
        if (!response.ok || !response.body) {
            throw new Error('Kaynak akışı başlatılamadı.');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const processLine = line => {
            if (!line.trim() || requestId !== detailRequestCounter) return;
            const event = JSON.parse(line);
            if (event.type === 'metadata') {
                manga = applyProgressiveMetadata(mangaId, manga, event);
                activeManga = manga;
            } else if (event.type === 'search_plan') {
                plannedSources = Array.isArray(event.sources) ? event.sources : [];
                if (statusText) {
                    const names = sourceNamesText(plannedSources.slice(0, 3).map(item => item.name));
                    statusText.textContent = names
                        ? `${names} üzerinde manga aranıyor…`
                        : 'Etkin kaynak bulunamadı; Eklentiler bölümünden kaynak kurabilirsin.';
                }
            } else if (event.type === 'source_status') {
                if (event.source_name) failedSourceNames.push(event.source_name);
                if (statusText && event.message) statusText.textContent = event.message;
                if (
                    event.status === 'ambiguous'
                    && typeof window.renderSourceBindingCandidates === 'function'
                ) {
                    window.renderSourceBindingCandidates(mangaId, event.candidates || []);
                }
            } else if (event.type === 'source') {
                loadResolvedSource(event.source);
            } else if (
                event.type === 'complete'
                && Number(event.source_count || 0) === 0
                && typeof window.renderSourceBindingRetry === 'function'
            ) {
                window.renderSourceBindingRetry(mangaId);
            }
        };

        while (true) {
            const { value, done } = await reader.read();
            if (requestId !== detailRequestCounter) {
                await reader.cancel();
                return;
            }
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            lines.forEach(processLine);
            if (done) break;
        }
        if (buffer.trim()) processLine(buffer);
        await Promise.allSettled([...pendingSourceFetches]);

        if (requestId !== detailRequestCounter) return;
        if (!firstSourceShown) {
            loader.classList.add('hidden');
            showSourceResolutionEmpty();
            updateLangTabs(false);
        }

        if (status) {
            status.classList.add(firstSourceShown ? 'complete' : 'warning');
            const icon = status.querySelector('i');
            if (icon) icon.className = firstSourceShown
                ? 'fa-solid fa-circle-check'
                : 'fa-solid fa-triangle-exclamation';
            if (statusText) {
                statusText.textContent = firstSourceShown
                    ? `${allFetchedSources.length} kaynak hazır. Manga okunabilir.`
                    : `${failedSourceNames.length || plannedSources.length} kaynak denendi; okunabilir bölüm bulunamadı.`;
            }
            setTimeout(() => {
                if (requestId === detailRequestCounter) status.classList.add('hidden');
            }, 2200);
        }
    } catch (error) {
        if (error?.name === 'AbortError') return;
        if (requestId !== detailRequestCounter) return;
        console.error('Aşamalı kaynak yükleme hatası:', error);
        loader.classList.add('hidden');
        if (!firstSourceShown) {
            listBody.innerHTML = `<tr><td colspan="4"><div class="source-resolution-message error"><i class="fa-solid fa-cloud-arrow-down"></i><div><strong>Kaynak hizmetine şu anda ulaşılamıyor.</strong><small>MangaX mevcut kaynaklarını koruyor. Bağlantını kontrol edip ayrıntı penceresini yeniden açarak tekrar deneyebilirsin.</small></div></div></td></tr>`;
        }
        if (status) status.classList.add('hidden');
    }
}

function changeMangaSource(sourceId, preferredLang = null) {
    const src = allFetchedSources.find(x => x.id === sourceId);
    if (src) {
        if (activeManga && activeManga.id && activeManga.id.startsWith("anilist_")) {
            // Keep activeManga (AniList metadata) as is!
        } else {
            activeManga = src.details;
        }
        activeChapters = src.chapters;
        scheduleKnownChapterSync(activeManga?.id || src.details?.id, src.chapters);
        
        // Re-render details immediately
        document.getElementById('manga-detail-title').textContent = activeManga.title;
        document.getElementById('manga-detail-desc').textContent = activeManga.description || "Açıklama bulunmuyor.";
        window.MangaXSafeDOM.setImageSource(document.getElementById('manga-detail-cover'), activeManga.cover_url);
        window.MangaXSafeDOM.setBackgroundImage(document.getElementById('manga-detail-banner'), activeManga.banner_url || activeManga.cover_url);
        document.getElementById('manga-detail-year').textContent = activeManga.year || "";
        
        const statusBadge = document.getElementById('manga-detail-status');
        statusBadge.textContent = activeManga.status === 'ongoing' ? 'Devam Ediyor' : 'Tamamlandı';
        statusBadge.className = `manga-status-badge ${activeManga.status}`;
        
        renderMangaDetailTags(activeManga.tags);
        
        // Dil sekmesinden gelindiyse o dili koru; dropdown değişiminde mevcut
        // kaynak için varsayılan dil önceliğini kullan.
        const hasTr = activeChapters.some(c => c.language === 'tr');
        const hasPreferredLang = preferredLang && activeChapters.some(
            chapter => chapter.language === preferredLang
        );
        activeLang = hasPreferredLang ? preferredLang : (hasTr ? 'tr' : 'en');
        updateLangTabs(hasTr);

        const sourceSelector = document.getElementById('manga-source-select');
        if (sourceSelector) sourceSelector.value = sourceId;
        
        // Populate group selector
        const chaptersForLang = activeChapters.filter(c => c.language === activeLang);
        populateGroupSelector(chaptersForLang);
        
        renderChapters();
    }
}

function updateLangTabs(hasTr) {
    const trTab = document.querySelector('.lang-tab[data-lang="tr"]');
    const enTab = document.querySelector('.lang-tab[data-lang="en"]');
    
    if (activeLang === 'tr') {
        trTab.classList.add('active');
        enTab.classList.remove('active');
    } else {
        enTab.classList.add('active');
        trTab.classList.remove('active');
    }
}

function filterChaptersByLang(lang) {
    // Bu dilde en çok bölümü olan kaynak, selector'daki "Önerilen" kaynaktır.
    if (allFetchedSources.length > 0) {
        const recommendedSource = allFetchedSources
            .map(source => ({
                source,
                languageCount: source.chapters.filter(
                    chapter => chapter.language === lang
                ).length
            }))
            .filter(item => item.languageCount > 0)
            .sort((a, b) => b.languageCount - a.languageCount)[0];

        if (recommendedSource) {
            changeMangaSource(recommendedSource.source.id, lang);
            return;
        }

        showToast(
            lang === 'tr'
                ? 'Bu manga için Türkçe kaynak bulunamadı.'
                : 'Bu manga için İngilizce kaynak bulunamadı.',
            'info'
        );
        return;
    }

    // Çevrimdışı/tek kaynak görünümünde mevcut bölümler üzerinde normal filtrele.
    activeLang = lang;
    document.querySelectorAll('.lang-tab').forEach(tab => {
        if (tab.getAttribute('data-lang') === lang) {
            tab.classList.add('active');
        } else {
            tab.classList.remove('active');
        }
    });
    // Populate group selector with active language chapters
    const chaptersForLang = activeChapters.filter(c => c.language === activeLang);
    populateGroupSelector(chaptersForLang);
    
    renderChapters();
}

function populateGroupSelector(chaptersForLang) {
    const select = document.getElementById('chapter-group-select');
    if (!select) return;
    
    select.innerHTML = '<option value="">Tüm Gruplar</option>';
    activeGroup = "";
    
    const groupCounts = {};
    chaptersForLang.forEach(c => {
        const grp = c.group || "No Group";
        groupCounts[grp] = (groupCounts[grp] || 0) + 1;
    });
    
    const groups = Object.keys(groupCounts).map(name => ({
        name,
        count: groupCounts[name]
    }));
    
    if (groups.length === 0) return;
    
    groups.sort((a, b) => b.count - a.count);
    
    const recGroup = groups[0].name;
    
    groups.forEach(g => {
        const option = document.createElement('option');
        option.value = g.name;
        if (g.name === recGroup) {
            option.textContent = `${g.name} (${g.count} Bölüm - Önerilen)`;
            option.selected = true;
            activeGroup = g.name;
        } else {
            option.textContent = `${g.name} (${g.count} Bölüm)`;
        }
        select.appendChild(option);
    });
}

function handleChapterGroupFilter() {
    const select = document.getElementById('chapter-group-select');
    if (select) {
        activeGroup = select.value;
        renderChapters();
    }
}

function renderChapters() {
    const listBody = document.getElementById('chapters-list-body');
    listBody.innerHTML = "";
    
    // 1. Language filter
    let filtered = activeChapters.filter(c => c.language === activeLang);
    
    // 2. Group filter
    if (activeGroup) {
        filtered = filtered.filter(c => (c.group || "No Group") === activeGroup);
    }
    
    // 3. Search query filter
    if (chapterSearchQuery) {
        const query = chapterSearchQuery.toLowerCase();
        filtered = filtered.filter(c => 
            c.chapter.toLowerCase().includes(query) || 
            (c.title && c.title.toLowerCase().includes(query))
        );
    }
    
    // 3. Sort chapters
    function getChapterNum(c) {
        try {
            return parseFloat(c.chapter || "0");
        } catch (e) {
            return 0;
        }
    }
    
    filtered.sort((a, b) => {
        const numA = getChapterNum(a);
        const numB = getChapterNum(b);
        return chapterSortOrder === 'asc' ? numA - numB : numB - numA;
    });
    
    if (filtered.length === 0) {
        listBody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-secondary);">Bölüm bulunamadı.</td></tr>`;
        return;
    }
    
    // Check local database for downloads
    const localManga = libraryData.mangas[activeManga.id] || null;
    const downloadedChapters = localManga ? (localManga.downloaded_chapters || {}) : {};
    
    filtered.forEach(ch => {
        const tr = document.createElement('tr');
        tr.dataset.chapterId = ch.id;
        const { element } = window.MangaXSafeDOM;
        const numberCell = element('td', { text: `Bölüm ${ch.chapter ?? ''}` });
        numberCell.style.fontWeight = '700';
        const titleCell = element('td', { text: ch.title || 'Başlıksız Bölüm' });
        const groupCell = element('td', { text: ch.group || 'Scanlation Group' });
        groupCell.style.color = 'var(--text-secondary)';
        const actionsCell = element('td', { className: 'chapter-actions-cell' });
        actionsCell.appendChild(typeof buildChapterActions === 'function'
            ? buildChapterActions(ch, downloadedChapters)
            : buildReaderOnlyChapterActions(ch, downloadedChapters));
        tr.append(numberCell, titleCell, groupCell, actionsCell);
        listBody.appendChild(tr);
    });
}


function closeDetailsModal() {
    detailRequestCounter++; // Devam eden kaynak ve bölüm isteklerini geçersiz kıl.
    detailRequestController?.abort();
    detailRequestController = null;
    document.getElementById('details-modal').classList.remove('active');
    detailOnlineMode = false; // Reset to default offline view on modal close
}

function toggleOnlineMode(isOnline) {
    detailOnlineMode = isOnline;
    if (activeManga) {
        viewMangaDetails(activeManga.id);
    }
}
