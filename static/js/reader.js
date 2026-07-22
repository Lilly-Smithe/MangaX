// Reader State
const READER_MODE_STORAGE_KEY = 'mangax-reader-mode';
const READER_PREFERENCES_STORAGE_KEY = 'mangax-reader-preferences-v1';
const READER_DIMENSIONS_STORAGE_KEY = 'mangax-reader-page-dimensions-v1';
const DEFAULT_READER_PREFERENCES = Object.freeze({
    mode: 'webtoon',
    spread: 'single',
    fit: 'page',
    zoom: 100,
    brightness: 100,
    background: 'black',
    autoNext: false,
    coverSingle: true,
    spreadOffset: 0,
});

function getSavedReaderPreferences() {
    try {
        const saved = JSON.parse(localStorage.getItem(READER_PREFERENCES_STORAGE_KEY) || '{}');
        const preferences = { ...DEFAULT_READER_PREFERENCES, ...saved };
        // Eski sürümlerdeki yön tercihi artık desteklenmiyor. Klasik okuyucu
        // sabit manga düzenini kullanır ve eski değer yeni profile taşınmaz.
        delete preferences.direction;
        if (!['webtoon', 'classic'].includes(preferences.mode)) preferences.mode = 'webtoon';
        if (!['single', 'double'].includes(preferences.spread)) preferences.spread = 'single';
        if (!['page', 'width'].includes(preferences.fit)) preferences.fit = 'page';
        if (!['black', 'charcoal', 'sepia'].includes(preferences.background)) preferences.background = 'black';
        preferences.zoom = Math.max(50, Math.min(250, Number(preferences.zoom) || 100));
        preferences.brightness = Math.max(35, Math.min(140, Number(preferences.brightness) || 100));
        preferences.autoNext = Boolean(preferences.autoNext);
        preferences.coverSingle = preferences.coverSingle !== false;
        preferences.spreadOffset = Number(preferences.spreadOffset) === 1 ? 1 : 0;
        return preferences;
    } catch (_) {
        return { ...DEFAULT_READER_PREFERENCES };
    }
}

function saveReaderPreferences() {
    try {
        localStorage.setItem(READER_PREFERENCES_STORAGE_KEY, JSON.stringify(readerPreferences));
        localStorage.setItem(READER_MODE_STORAGE_KEY, readerPreferences.mode);
    } catch (_) { /* tercihler bu oturumda korunmaya devam eder */ }
}

function getSavedReaderMode() {
    try {
        const preferences = getSavedReaderPreferences();
        const legacy = localStorage.getItem(READER_MODE_STORAGE_KEY);
        if (!localStorage.getItem(READER_PREFERENCES_STORAGE_KEY) && legacy === 'classic') {
            return 'classic';
        }
        if (preferences.mode) return preferences.mode;
        const saved = localStorage.getItem(READER_MODE_STORAGE_KEY);
        return saved === 'classic' ? 'classic' : 'webtoon';
    } catch (_) {
        return 'webtoon';
    }
}

function saveReaderMode(mode) {
    readerPreferences.mode = mode;
    saveReaderPreferences();
    try {
        localStorage.setItem(READER_MODE_STORAGE_KEY, mode);
    } catch (_) { /* depolama kapalıysa seçim yalnızca bu oturumda korunur */ }
}

let readerMangaId = "";
let readerChapterId = "";
let readerIsOnline = true;
let readerPages = [];
let readerPageIndex = 0;
let readerPreferences = getSavedReaderPreferences();
let readerMode = getSavedReaderMode(); // 'webtoon' or 'classic'
readerPreferences.mode = readerMode;
saveReaderPreferences();
let readerAtEndCard = false;
let controlsTimeout = null;
let _readerFetchController = null; // AbortController for current fetch
let _lastClassicWheelAt = 0;
let _lastClassicWheelDirection = 0;
let _readerGlobalControlsBound = false;
let _readerAutoNextTimer = null;
let _readerAutoNextRemaining = 0;
let _pendingProgress = null;
let _progressSavePromise = Promise.resolve();
let _readerProgressReady = false;
const readerFallbackPageCache = new Map();
const readerPageAspectRatios = loadReaderPageDimensions();
const readerClassicPrefetch = new Map();
let readerGlobalPreferences = { ...readerPreferences };
let readerUsingMangaProfile = false;
let readerBookmarks = [];
let readerFocusMode = false;
let readerLastLoadFailure = null;
const WEBTOON_PRELOAD_RADIUS = 3;
const WEBTOON_RETAIN_RADIUS = 6;
let readerDimensionSaveTimer = null;

function loadReaderPageDimensions() {
    try {
        const entries = JSON.parse(localStorage.getItem(READER_DIMENSIONS_STORAGE_KEY) || '[]');
        return new Map(Array.isArray(entries) ? entries.slice(-1000) : []);
    } catch (_) {
        return new Map();
    }
}

function rememberReaderPageDimension(key, ratio) {
    readerPageAspectRatios.delete(key);
    readerPageAspectRatios.set(key, ratio);
    while (readerPageAspectRatios.size > 1000) readerPageAspectRatios.delete(readerPageAspectRatios.keys().next().value);
    if (readerDimensionSaveTimer) return;
    readerDimensionSaveTimer = setTimeout(flushReaderPageDimensions, 500);
}

function flushReaderPageDimensions() {
    if (readerDimensionSaveTimer) clearTimeout(readerDimensionSaveTimer);
    readerDimensionSaveTimer = null;
    try {
        localStorage.setItem(READER_DIMENSIONS_STORAGE_KEY, JSON.stringify([...readerPageAspectRatios.entries()]));
    } catch (_) { /* Boyut önbelleği isteğe bağlıdır. */ }
}

// Intersection observer for tracking page scrolling in Webtoon mode
let webtoonObserver = null;

// Loader helpers — use both classList AND inline style for reliability across browsers/cache
function _showLoader() {
    const el = document.getElementById('reader-loader');
    el.classList.remove('hidden');
    el.style.display = 'flex';
}
function _hideLoader() {
    const el = document.getElementById('reader-loader');
    el.classList.add('hidden');
    el.style.display = 'none';
}

function _setLoaderMessage(message) {
    const text = document.querySelector('#reader-loader p');
    if (text) text.textContent = message;
}

function getActiveReaderSourceName() {
    const source = allFetchedSources.find(item =>
        Array.isArray(item.chapters) && item.chapters.some(chapter => chapter.id === readerChapterId)
    );
    return source?.sourceName || (typeof getSourceLabel === 'function' ? getSourceLabel(readerMangaId) : 'Seçilen kaynak');
}

function normalizeReaderChapterNumber(value) {
    const cleaned = String(value ?? '')
        .trim()
        .toLocaleLowerCase('tr-TR')
        .replace(',', '.')
        .replace(/^(bölüm|bolum|chapter|ch)\s*/i, '')
        .replace(/\s+/g, ' ');
    const numeric = cleaned.match(/^\d+(?:\.\d+)?$/);
    return numeric ? String(Number(cleaned)) : cleaned;
}

function normalizeReaderChapterTitle(value) {
    return String(value || '')
        .toLocaleLowerCase('tr-TR')
        .normalize('NFKD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, ' ')
        .trim();
}

function readerChaptersMatch(current, candidate) {
    if (!current || !candidate) return false;
    const currentLanguage = current.language === 'en' ? 'en' : 'tr';
    const candidateLanguage = candidate.language === 'en' ? 'en' : 'tr';
    if (currentLanguage !== candidateLanguage) return false;

    const currentNumber = normalizeReaderChapterNumber(current.chapter);
    const candidateNumber = normalizeReaderChapterNumber(candidate.chapter);
    if (currentNumber && candidateNumber) return currentNumber === candidateNumber;

    const currentTitle = normalizeReaderChapterTitle(current.title);
    const candidateTitle = normalizeReaderChapterTitle(candidate.title);
    return Boolean(currentTitle && candidateTitle && currentTitle === candidateTitle);
}

function getReaderSourceForChapter(chapterId) {
    return allFetchedSources.find(source =>
        Array.isArray(source.chapters)
        && source.chapters.some(chapter => chapter.id === chapterId)
    ) || null;
}

async function fetchReaderJson(url, { signal, timeoutMs = 15000 } = {}) {
    const controller = new AbortController();
    let timedOut = false;
    const abortFromParent = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener('abort', abortFromParent, { once: true });
    const timeout = setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);

    try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error(`Sunucu hatası: ${response.status}`);
        return await response.json();
    } catch (error) {
        if (timedOut) {
            const timeoutError = new Error('Kaynak zamanında yanıt vermedi.');
            timeoutError.name = 'ReaderSourceTimeoutError';
            throw timeoutError;
        }
        throw error;
    } finally {
        clearTimeout(timeout);
        signal?.removeEventListener('abort', abortFromParent);
    }
}

async function fetchReaderChapterPages(mangaId, chapterId, { signal, timeoutMs = 15000 } = {}) {
    const data = await fetchReaderJson(
        `/api/manga/${encodeURIComponent(mangaId)}/chapters/${encodeURIComponent(chapterId)}/pages?online=true`,
        { signal, timeoutMs }
    );
    return Array.isArray(data.pages) ? data.pages : [];
}

async function loadReaderFallbackSources(signal) {
    // Katalog kimliği bir kaynak kimliği değildir. MAL, AniList veya başka bir
    // kanonik kimlikle gelen Full kayıtları aynı resolve/binding hattını kullanır.
    if (!readerIsOnline || document.body.dataset.appEdition !== 'full') return;
    const libraryManga = libraryData?.mangas?.[readerMangaId] || activeManga || {};
    const catalogBacked = readerMangaId.startsWith('mal_')
        || readerMangaId.startsWith('anilist_')
        || Number(libraryManga.mal_id) > 0
        || Number(libraryManga.anilist_id) > 0;
    if (!catalogBacked) return;
    const knownSourceIds = new Set(allFetchedSources.map(source => source.id));
    const resolved = await fetchReaderJson(
        `/api/manga/resolve/${encodeURIComponent(readerMangaId)}`,
        { signal, timeoutMs: 45000 }
    );
    const missingSources = (resolved.sources || []).filter(source =>
        source.id && !knownSourceIds.has(source.id)
    );
    const mangaForCount = activeManga || resolved;
    const loaded = await Promise.allSettled(missingSources.map(async source => {
        const chapters = await fetchReaderJson(
            buildChapterApiUrl(source.id, mangaForCount),
            { signal, timeoutMs: 15000 }
        );
        if (!Array.isArray(chapters) || chapters.length === 0) return null;
        return {
            id: source.id,
            sourceName: source.source || getSourceLabel(source.id),
            details: activeManga || resolved,
            chapters,
            count: chapters.length,
        };
    }));
    loaded.forEach(result => {
        const source = result.status === 'fulfilled' ? result.value : null;
        if (source && !allFetchedSources.some(item => item.id === source.id)) {
            allFetchedSources.push(source);
        }
    });
    if (typeof updateProgressiveSourceSelector === 'function') {
        updateProgressiveSourceSelector(
            document.getElementById('manga-source-selector-wrapper'),
            document.getElementById('manga-source-select')
        );
    }
}

function getReaderFallbackCandidates(currentChapter, failedSourceId, triedSourceIds) {
    const candidates = [];
    allFetchedSources.forEach(source => {
        if (!source?.id || source.id === failedSourceId || triedSourceIds.has(source.id)) return;
        const chapter = (source.chapters || []).find(item => readerChaptersMatch(currentChapter, item));
        if (chapter) candidates.push({ source, chapter });
    });
    const priority = getAppPreference('source_priority', []);
    return candidates.sort((a, b) => {
        const aIndex = priority.indexOf(a.source.id);
        const bIndex = priority.indexOf(b.source.id);
        return (aIndex < 0 ? 9999 : aIndex) - (bIndex < 0 ? 9999 : bIndex);
    });
}

async function tryReaderSourceFallback(currentChapter, failureReason, triedSourceIds, signal) {
    if (!readerIsOnline || !currentChapter) return false;
    const fallbackMode = getAppPreference('fallback_mode', 'ask');
    if (fallbackMode === 'off') return false;
    const failedSource = getReaderSourceForChapter(readerChapterId);
    const failedSourceId = failedSource?.id || '';
    if (failedSourceId) triedSourceIds.add(failedSourceId);

    _setLoaderMessage(`${failedSource?.sourceName || getActiveReaderSourceName()} yanıt vermedi. Alternatif kaynaklar kontrol ediliyor...`);
    let candidates = getReaderFallbackCandidates(currentChapter, failedSourceId, triedSourceIds);
    if (candidates.length === 0) {
        try {
            await loadReaderFallbackSources(signal);
            candidates = getReaderFallbackCandidates(currentChapter, failedSourceId, triedSourceIds);
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            console.warn('[Reader] Alternatif kaynaklar yüklenemedi:', error);
        }
    }

    for (const candidate of candidates) {
        triedSourceIds.add(candidate.source.id);
        _setLoaderMessage(`${candidate.source.sourceName} üzerinde Bölüm ${candidate.chapter.chapter} kontrol ediliyor...`);
        try {
            const pages = await fetchReaderChapterPages(readerMangaId, candidate.chapter.id, {
                signal,
                timeoutMs: 12000,
            });
            if (pages.length === 0) continue;
            readerFallbackPageCache.set(candidate.chapter.id, pages);
            _hideLoader();
            const accepted = fallbackMode === 'auto' || await showAppConfirm({
                title: 'Alternatif Kaynak Bulundu',
                message: `${failedSource?.sourceName || getActiveReaderSourceName()} ${failureReason}. Aynı Bölüm ${candidate.chapter.chapter}, ${candidate.source.sourceName} üzerinde bulundu. Bu kaynağa geçilsin mi?`,
                confirmText: `${candidate.source.sourceName} ile Oku`,
                cancelText: 'Burada Kal',
                icon: 'fa-shuffle',
            });
            if (!accepted) {
                readerFallbackPageCache.delete(candidate.chapter.id);
                return false;
            }

            changeMangaSource(candidate.source.id, candidate.chapter.language || null);
            showToast(`${candidate.source.sourceName} kaynağına geçildi.`, 'success');
            const nextTriedSourceIds = [...triedSourceIds];
            setTimeout(() => {
                startReading(readerMangaId, candidate.chapter.id, true, {
                    fallbackTriedSourceIds: nextTriedSourceIds,
                });
            }, 0);
            return true;
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            console.warn(`[Reader] ${candidate.source.sourceName} alternatifi kullanılamadı:`, error);
        }
    }
    return false;
}

function prepareReaderForResume(mangaTitle, chapterNum) {
    const overlay = document.getElementById('reader-overlay');
    overlay.classList.add('active');
    overlay.classList.remove('controls-hidden');
    document.getElementById('reader-manga-title').textContent = mangaTitle || 'Manga';
    document.getElementById('reader-chapter-title').textContent = `Bölüm ${chapterNum || '?'} hazırlanıyor`;
    const indicator = document.getElementById('reader-page-indicator');
    if (indicator) indicator.textContent = '— / —';
    _setLoaderMessage('Kaynak ve bölüm hazırlanıyor...');
    _showLoader();
}

function cancelReaderPreparation() {
    _hideLoader();
    document.getElementById('reader-overlay').classList.remove('active');
    _setLoaderMessage('Sayfalar Yükleniyor...');
}

function hideReaderLoadError() {
    const panel = document.getElementById('reader-error-state');
    if (panel) panel.classList.add('hidden');
}

function showReaderLoadError(title, message, { online = readerIsOnline, reason = 'error' } = {}) {
    readerLastLoadFailure = { mangaId: readerMangaId, chapterId: readerChapterId, online, reason };
    const panel = document.getElementById('reader-error-state');
    if (!panel) return;
    panel.dataset.online = String(Boolean(online));
    panel.querySelector('#reader-error-title').textContent = title;
    panel.querySelector('#reader-error-message').textContent = message;
    panel.classList.remove('hidden');
}

async function retryReaderChapter() {
    if (!readerLastLoadFailure) return;
    const failure = { ...readerLastLoadFailure };
    hideReaderLoadError();
    await startReading(failure.mangaId, failure.chapterId, failure.online);
}

async function retryReaderWithAlternativeSource() {
    if (!readerIsOnline) return;
    hideReaderLoadError();
    const chapter = activeChapters.find(item => item.id === readerChapterId);
    const switched = await tryReaderSourceFallback(chapter, 'yeniden yüklenemedi', new Set(), new AbortController().signal);
    if (!switched) {
        showReaderLoadError('Alternatif kaynak bulunamadı', 'Kurulu kaynaklarda bu bölümün okunabilir başka bir kopyası bulunamadı.', { online: true, reason: 'no_alternative' });
    }
}

function validReaderExternalUrl(value) {
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_) {
        return '';
    }
}

function renderReaderPageFailure(image, pageIndex) {
    const shell = image.closest('.reader-page-shell');
    if (!shell || shell.querySelector('.reader-page-failure')) return;
    shell.classList.add('page-failed');
    const panel = document.createElement('div');
    panel.className = 'reader-page-failure';
    const title = document.createElement('strong');
    title.textContent = `Sayfa ${pageIndex + 1} yüklenemedi`;
    const copy = document.createElement('p');
    copy.textContent = readerIsOnline ? 'Bağlantı veya bölüm kaynağı geçici olarak yanıt vermiyor olabilir.' : 'Yerel sayfa dosyası eksik veya okunamıyor olabilir.';
    const actions = document.createElement('div');
    actions.className = 'reader-page-failure-actions';
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'btn btn-primary'; retry.textContent = 'Tekrar Dene';
    retry.addEventListener('click', event => {
        event.stopPropagation();
        panel.remove(); shell.classList.remove('page-failed');
        image.dataset.retryCount = String((Number(image.dataset.retryCount) || 0) + 1);
        image.src = image.dataset.src;
    });
    actions.appendChild(retry);
    if (readerIsOnline) {
        const alternative = document.createElement('button');
        alternative.type = 'button'; alternative.className = 'btn btn-secondary'; alternative.textContent = 'Alternatif Kaynak';
        alternative.addEventListener('click', event => { event.stopPropagation(); retryReaderWithAlternativeSource(); });
        actions.appendChild(alternative);
        const pageUrl = validReaderExternalUrl(image.dataset.src);
        if (pageUrl && !pageUrl.startsWith(window.location.origin)) {
            const open = document.createElement('button');
            open.type = 'button'; open.className = 'btn btn-secondary'; open.textContent = 'Tarayıcıda Aç';
            open.addEventListener('click', event => { event.stopPropagation(); window.open(pageUrl, '_blank', 'noopener,noreferrer'); });
            actions.appendChild(open);
        }
    }
    const skip = document.createElement('button');
    skip.type = 'button'; skip.className = 'btn btn-secondary'; skip.textContent = 'Sayfayı Atla';
    skip.addEventListener('click', event => { event.stopPropagation(); jumpToPage(Math.min(pageIndex + 1, readerPages.length - 1)); });
    actions.appendChild(skip);
    panel.append(title, copy, actions);
    shell.appendChild(panel);
}

function currentReaderManga() {
    return libraryData?.mangas?.[readerMangaId] || activeManga || null;
}

function normalizeReaderProfile(profile = {}) {
    const merged = { ...DEFAULT_READER_PREFERENCES, ...profile };
    if (!['webtoon', 'classic'].includes(merged.mode)) merged.mode = 'webtoon';
    if (!['single', 'double'].includes(merged.spread)) merged.spread = 'single';
    if (!['page', 'width'].includes(merged.fit)) merged.fit = 'page';
    if (!['black', 'charcoal', 'sepia'].includes(merged.background)) merged.background = 'black';
    merged.zoom = Math.max(50, Math.min(250, Number(merged.zoom) || 100));
    merged.brightness = Math.max(35, Math.min(140, Number(merged.brightness) || 100));
    merged.autoNext = Boolean(merged.autoNext);
    merged.coverSingle = merged.coverSingle !== false;
    merged.spreadOffset = Number(merged.spreadOffset) === 1 ? 1 : 0;
    merged.enabled = Boolean(profile.enabled);
    return merged;
}

function loadMangaReaderState(mangaId) {
    const manga = libraryData?.mangas?.[mangaId] || null;
    const profile = normalizeReaderProfile(manga?.reader_profile || {});
    readerGlobalPreferences = getSavedReaderPreferences();
    readerUsingMangaProfile = Boolean(profile.enabled);
    readerPreferences = readerUsingMangaProfile ? profile : { ...readerGlobalPreferences };
    readerMode = readerPreferences.mode;
    readerBookmarks = Array.isArray(manga?.page_bookmarks) ? [...manga.page_bookmarks] : [];
    renderReaderBookmarks();
}

async function persistMangaReaderProfile() {
    if (!readerMangaId || !readerUsingMangaProfile) return;
    const response = await fetch(`/api/library/${encodeURIComponent(readerMangaId)}/reader-profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            enabled: true,
            mode: readerPreferences.mode,
            spread: readerPreferences.spread,
            fit: readerPreferences.fit,
            zoom: readerPreferences.zoom,
            brightness: readerPreferences.brightness,
            background: readerPreferences.background,
            auto_next: readerPreferences.autoNext,
            cover_single: readerPreferences.coverSingle,
            spread_offset: readerPreferences.spreadOffset,
        }),
    });
    if (!response.ok) throw new Error('Manga okuyucu profili kaydedilemedi');
    const result = await response.json();
    if (result.manga) libraryData.mangas[readerMangaId] = result.manga;
}

async function toggleMangaReaderProfile(enabled) {
    if (!readerMangaId) return;
    readerUsingMangaProfile = Boolean(enabled);
    if (!enabled) {
        readerPreferences = { ...readerGlobalPreferences };
        readerMode = readerPreferences.mode;
    }
    try {
        const payload = normalizeReaderProfile({ ...readerPreferences, enabled });
        const response = await fetch(`/api/library/${encodeURIComponent(readerMangaId)}/reader-profile`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...payload, auto_next: payload.autoNext, cover_single: payload.coverSingle, spread_offset: payload.spreadOffset }),
        });
        if (!response.ok) throw new Error('Profil kaydedilemedi');
        const result = await response.json();
        if (result.manga) libraryData.mangas[readerMangaId] = result.manga;
        applyReaderPreferences({ rerender: true });
        showToast(enabled ? 'Bu mangaya özel okuyucu profili açıldı.' : 'Genel okuyucu ayarlarına dönüldü.', 'success');
    } catch (error) {
        readerUsingMangaProfile = !enabled;
        syncReaderPreferenceControls();
        showToast('Okuyucu profili kaydedilemedi.', 'error');
    }
}

function syncReaderPreferenceControls() {
    const values = {
        'reader-spread': readerPreferences.spread,
        'reader-fit': readerPreferences.fit,
        'reader-zoom': readerPreferences.zoom,
        'reader-brightness': readerPreferences.brightness,
        'reader-background': readerPreferences.background,
        'reader-spread-offset': readerPreferences.spreadOffset,
    };
    Object.entries(values).forEach(([id, value]) => {
        const control = document.getElementById(id);
        if (control) control.value = value;
    });
    const autoNext = document.getElementById('reader-auto-next');
    if (autoNext) autoNext.checked = readerPreferences.autoNext;
    const coverSingle = document.getElementById('reader-cover-single');
    if (coverSingle) coverSingle.checked = readerPreferences.coverSingle;
    const mangaProfile = document.getElementById('reader-use-manga-profile');
    if (mangaProfile) mangaProfile.checked = readerUsingMangaProfile;
    const zoomValue = document.getElementById('reader-zoom-value');
    if (zoomValue) zoomValue.textContent = `%${readerPreferences.zoom}`;
    const brightnessValue = document.getElementById('reader-brightness-value');
    if (brightnessValue) brightnessValue.textContent = `%${readerPreferences.brightness}`;
    document.querySelectorAll('[data-reader-background]').forEach(button => {
        button.classList.toggle('active', button.dataset.readerBackground === readerPreferences.background);
    });
    document.querySelectorAll('.classic-only-setting').forEach(element => {
        element.classList.toggle('setting-disabled', readerMode !== 'classic');
        element.querySelectorAll('select, input, button').forEach(control => {
            control.disabled = readerMode !== 'classic';
        });
    });
}

function applyReaderPreferences({ rerender = false } = {}) {
    const overlay = document.getElementById('reader-overlay');
    const viewport = document.getElementById('reader-viewport');
    overlay.style.setProperty('--reader-zoom', String(readerPreferences.zoom / 100));
    overlay.style.setProperty('--reader-brightness', String(readerPreferences.brightness / 100));
    overlay.dataset.readerSpread = readerPreferences.spread;
    overlay.dataset.readerFit = readerPreferences.fit;
    overlay.dataset.readerBackground = readerPreferences.background;
    viewport.classList.toggle('reader-fit-width', readerPreferences.fit === 'width');
    viewport.classList.toggle('reader-fit-page', readerPreferences.fit === 'page');
    syncReaderPreferenceControls();
    if (rerender && readerPages.length) {
        renderReaderPages();
        jumpToPage(readerPageIndex, { behavior: 'auto' });
    }
}

function setReaderPreference(key, value) {
    if (!(key in DEFAULT_READER_PREFERENCES)) return;
    if (key === 'zoom') value = Math.max(50, Math.min(250, Number(value) || 100));
    if (key === 'brightness') value = Math.max(35, Math.min(140, Number(value) || 100));
    if (key === 'autoNext') value = Boolean(value);
    if (key === 'coverSingle') value = Boolean(value);
    if (key === 'spreadOffset') value = Number(value) === 1 ? 1 : 0;
    readerPreferences[key] = value;
    if (key === 'mode') readerMode = value;
    if (readerUsingMangaProfile) {
        persistMangaReaderProfile().catch(() => showToast('Manga okuyucu profili kaydedilemedi.', 'error'));
    } else {
        readerGlobalPreferences = { ...readerPreferences };
        saveReaderPreferences();
    }
    applyReaderPreferences({ rerender: ['spread', 'coverSingle', 'spreadOffset'].includes(key) });
    if (!readerPreferences.autoNext) cancelAutoNext();
}

function saveReaderPreferenceDefaults(values = {}) {
    const next = { ...readerPreferences, ...values };
    if (!['webtoon', 'classic'].includes(next.mode)) next.mode = 'webtoon';
    if (!['single', 'double'].includes(next.spread)) next.spread = 'single';
    if (!['page', 'width'].includes(next.fit)) next.fit = 'page';
    if (!['black', 'charcoal', 'sepia'].includes(next.background)) next.background = 'black';
    next.zoom = Math.max(50, Math.min(250, Number(next.zoom) || 100));
    next.brightness = Math.max(35, Math.min(140, Number(next.brightness) || 100));
    next.autoNext = Boolean(next.autoNext);
    next.coverSingle = next.coverSingle !== false;
    next.spreadOffset = Number(next.spreadOffset) === 1 ? 1 : 0;
    readerPreferences = next;
    readerMode = next.mode;
    saveReaderPreferences();
    return { ...readerPreferences };
}

function adjustReaderZoom(delta) {
    setReaderPreference('zoom', readerPreferences.zoom + delta);
}

function resetReaderPreferences() {
    readerPreferences = { ...DEFAULT_READER_PREFERENCES, mode: readerMode };
    if (readerUsingMangaProfile) {
        persistMangaReaderProfile().catch(() => showToast('Manga okuyucu profili kaydedilemedi.', 'error'));
    } else {
        readerGlobalPreferences = { ...readerPreferences };
        saveReaderPreferences();
    }
    applyReaderPreferences({ rerender: true });
    showToast('Okuyucu görünüm ayarları sıfırlandı.', 'success');
}

function toggleReaderSettings(force) {
    const panel = document.getElementById('reader-settings-panel');
    const shouldOpen = typeof force === 'boolean' ? force : !panel.classList.contains('active');
    panel.classList.toggle('active', shouldOpen);
    panel.setAttribute('aria-hidden', String(!shouldOpen));
    if (shouldOpen) {
        closeReaderShortcuts();
        showReaderControls();
    }
}

function openReaderShortcuts() {
    toggleReaderSettings(false);
    const dialog = document.getElementById('reader-shortcuts-modal');
    dialog.classList.add('active');
    dialog.setAttribute('aria-hidden', 'false');
    showReaderControls();
}

function closeReaderShortcuts() {
    const dialog = document.getElementById('reader-shortcuts-modal');
    dialog.classList.remove('active');
    dialog.setAttribute('aria-hidden', 'true');
}

// Start reading a chapter
async function startReading(mangaId, chapterId, isOnline, options = {}) {
    await flushReaderProgress();
    cancelAutoNext();
    // Cancel any in-progress fetch from a previous call (prevents stale data showing up)
    if (_readerFetchController) {
        _readerFetchController.abort();
        _readerFetchController = null;
    }

    // Reset all state immediately
    readerMangaId = mangaId;
    readerChapterId = chapterId;
    readerIsOnline = isOnline;
    readerPages = [];
    readerPageIndex = 0;
    readerAtEndCard = false;
    _readerProgressReady = false;
    readerLastLoadFailure = null;
    hideReaderLoadError();
    loadMangaReaderState(mangaId);

    // Disconnect old observer
    if (webtoonObserver) {
        webtoonObserver.disconnect();
        webtoonObserver = null;
    }

    // Show overlay
    const overlay = document.getElementById('reader-overlay');
    overlay.classList.add('active');
    applyReaderPreferences();

    // Show loader immediately
    _setLoaderMessage('Sayfalar Yükleniyor...');
    _showLoader();

    // Clear old pages safely:
    // end-chapter-card gets moved INTO pages-wrapper by webtoon renderReaderPages(),
    // so rescue it first to avoid losing it when we clear innerHTML.
    const endCard = document.getElementById('end-chapter-card');
    if (endCard) {
        const wrapper = document.getElementById('pages-wrapper');
        if (endCard.parentElement === wrapper) {
            // Move back to reader-viewport before clearing
            document.getElementById('reader-viewport').appendChild(endCard);
        }
        endCard.classList.add('hidden');
    }
    document.getElementById('pages-wrapper').innerHTML = "";

    // Reset page indicator
    const indEl = document.getElementById('reader-page-indicator');
    if (indEl) indEl.textContent = "— / —";
    syncPageRange(0, 0);


    // Update header
    const mangaTitle = activeManga ? activeManga.title : "Manga";
    document.getElementById('reader-manga-title').textContent = mangaTitle;

    const chapter = activeChapters.find(c => c.id === chapterId);
    const chapterNum = chapter ? chapter.chapter : "?";
    const chapterTitle = chapter ? (chapter.title || "Başlıksız") : "";
    document.getElementById('reader-chapter-title').textContent = `Bölüm ${chapterNum} - ${chapterTitle}`;

    // Configure layout mode CSS classes
    setReaderModeLayout();

    // Fetch pages with abort support
    const requestController = new AbortController();
    _readerFetchController = requestController;
    const { signal } = requestController;
    const fallbackTriedSourceIds = new Set(options.fallbackTriedSourceIds || []);

    try {
        const cachedPages = isOnline ? readerFallbackPageCache.get(chapterId) : null;
        if (cachedPages) {
            readerFallbackPageCache.delete(chapterId);
            readerPages = cachedPages;
        } else if (isOnline) {
            readerPages = await fetchReaderChapterPages(mangaId, chapterId, {
                signal,
                timeoutMs: 18000,
            });
        } else {
            const data = await fetchReaderJson(
                `/api/local/manga/${encodeURIComponent(mangaId)}/chapters/${encodeURIComponent(chapterId)}/pages`,
                { signal, timeoutMs: 10000 }
            );
            readerPages = Array.isArray(data.pages) ? data.pages : [];
        }

        if (readerPages.length === 0) {
            const switched = isOnline && typeof tryReaderSourceFallback === 'function'
                ? await tryReaderSourceFallback(
                    chapter,
                    'bu bölüm için sayfa göndermedi',
                    fallbackTriedSourceIds,
                    signal
                )
                : false;
            if (switched) return;
            _hideLoader();
            showReaderLoadError('Bu bölümde sayfa bulunamadı', `${getActiveReaderSourceName()} sayfa göndermedi ve çalışan bir alternatif bulunamadı.`, { online: isOnline, reason: 'empty' });
            return;
        }

        // Hide loader and render pages
        _hideLoader();
        renderReaderPages();

        // Setup controls
        setupControlsAutohide();
        setupKeyboardControls();

        // Restore saved reading progress and register online reads in the library.
        const localManga = libraryData.mangas[mangaId] || null;
        let initialPage = 0;
        let initialOffset = 0;
        let initialPercent = 0;
        if (localManga && localManga.last_read_chapter === chapterId) {
            const savedPage = localManga.last_read_page || 0;
            if (savedPage > 0 && savedPage < readerPages.length) {
                initialPage = savedPage;
            }
            initialOffset = Number(localManga.last_read_offset) || 0;
            initialPercent = Number(localManga.last_read_percent) || 0;
        }
        if (initialPage > 0 || initialOffset > 0 || initialPercent > 0) {
            setTimeout(() => restoreReaderProgress(initialPage, initialOffset, initialPercent), 250);
        } else {
            _readerProgressReady = true;
            await saveProgress(0, 0, 0);
        }

    } catch (e) {
        if (e.name === 'AbortError') {
            // A newer startReading call was made — silently ignore this one
            return;
        }
        console.error("[Reader] Chapter load error:", e);
        let switched = false;
        try {
            switched = isOnline && typeof tryReaderSourceFallback === 'function' && await tryReaderSourceFallback(
                chapter,
                e.name === 'ReaderSourceTimeoutError' ? 'zamanında yanıt vermedi' : 'yanıt vermedi',
                fallbackTriedSourceIds,
                signal
            );
        } catch (fallbackError) {
            if (fallbackError.name === 'AbortError') return;
            console.warn('[Reader] Akıllı kaynak değiştirme tamamlanamadı:', fallbackError);
        }
        if (!switched) {
            _hideLoader();
            showReaderLoadError('Bölüm yüklenemedi', `${getActiveReaderSourceName()} yanıt vermedi. Tekrar deneyebilir veya başka bir kaynağı kontrol edebilirsin.`, { online: isOnline, reason: e.name || 'error' });
        }
    } finally {
        if (_readerFetchController === requestController) _readerFetchController = null;
    }
}

function setReaderModeLayout() {
    const overlay = document.getElementById('reader-overlay');
    const viewport = document.getElementById('reader-viewport');
    const modeSelect = document.getElementById('reader-mode');
    if (modeSelect) modeSelect.value = readerMode;

    viewport.classList.toggle('webtoon-mode', readerMode === 'webtoon');
    viewport.classList.toggle('classic-mode', readerMode === 'classic');
    if (readerMode === 'webtoon') {
        document.getElementById('reader-viewport').onscroll = handleWebtoonScroll;
        overlay.classList.remove('classic-active');
    } else {
        overlay.classList.add('classic-active');
    }

    readerPreferences.mode = readerMode;
    applyReaderPreferences();
    showReaderControls();
}

function changeReaderMode(mode) {
    if (!['webtoon', 'classic'].includes(mode)) return;
    readerMode = mode;
    readerPreferences.mode = mode;
    if (readerUsingMangaProfile) {
        persistMangaReaderProfile().catch(() => showToast('Manga okuyucu profili kaydedilemedi.', 'error'));
    } else {
        readerGlobalPreferences = { ...readerPreferences };
        saveReaderMode(readerMode);
    }
    setReaderModeLayout();
    renderReaderPages();
    jumpToPage(readerPageIndex);
}

function renderReaderPages() {
    const wrapper = document.getElementById('pages-wrapper');
    const endCard = document.getElementById('end-chapter-card');
    if (endCard && endCard.parentElement === wrapper) {
        document.getElementById('reader-viewport').appendChild(endCard);
    }
    wrapper.innerHTML = "";
    readerAtEndCard = false;

    if (webtoonObserver) {
        webtoonObserver.disconnect();
        webtoonObserver = null;
    }

    // Configure slider range
    syncPageRange(readerPageIndex, readerPages.length);
    updatePageIndicator();

    if (readerMode === 'webtoon') {
        webtoonObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                // Lazy görsel henüz yüklenmediyse yüksekliği 0 olabilir. Böyle
                // girişler bütün sayfaları sonda sanıp ilerleme çubuğunu bozar.
                if (entry.isIntersecting && entry.boundingClientRect.height > 20) {
                    const index = parseInt(entry.target.getAttribute('data-index'));
                    readerPageIndex = index;
                    updateWebtoonImageWindow(index);
                    updatePageIndicator();
                    updatePreciseWebtoonProgress(true);
                }
            });
        }, {
            root: document.getElementById('reader-viewport'),
            rootMargin: '0px',
            threshold: 0.3
        });

        readerPages.forEach((url, index) => {
            const shell = document.createElement('div');
            shell.className = 'reader-page-shell';
            shell.dataset.index = String(index);
            const img = document.createElement('img');
            img.className = "manga-page";
            img.setAttribute('data-index', index);
            img.dataset.src = url;
            const knownRatio = readerPageAspectRatios.get(`${readerMangaId}|${readerChapterId}|${index}`);
            shell.style.aspectRatio = knownRatio || '2 / 3';
            img.loading = "lazy";
            img.decoding = "async";
            img.onerror = () => renderReaderPageFailure(img, index);
            img.onload = () => {
                shell.classList.remove('page-failed');
                shell.querySelector('.reader-page-failure')?.remove();
                if (img.naturalWidth && img.naturalHeight) {
                    const ratio = `${img.naturalWidth} / ${img.naturalHeight}`;
                    shell.style.aspectRatio = ratio;
                    rememberReaderPageDimension(`${readerMangaId}|${readerChapterId}|${index}`, ratio);
                }
                if (index === readerPageIndex) updatePreciseWebtoonProgress(false);
            };
            shell.appendChild(img);
            wrapper.appendChild(shell);
            webtoonObserver.observe(shell);
        });
        updateWebtoonImageWindow(readerPageIndex);

        // Show end-of-chapter card at the bottom
        endCard.classList.remove('hidden');
        wrapper.appendChild(endCard);

    } else {
        document.getElementById('reader-viewport').onscroll = null;
        endCard.classList.add('hidden');
        showClassicSpread(readerPageIndex);
    }

    applyReaderPreferences();
}

function updateWebtoonImageWindow(centerIndex) {
    if (readerMode !== 'webtoon') return;
    const preloadRadius = getAdaptiveReaderPreloadRadius();
    const retainRadius = Math.max(WEBTOON_RETAIN_RADIUS, preloadRadius * 2);
    document.querySelectorAll('#pages-wrapper .manga-page').forEach(image => {
        const index = Number(image.dataset.index);
        const distance = Math.abs(index - centerIndex);
        if (distance <= preloadRadius && !image.getAttribute('src')) {
            image.src = image.dataset.src;
        } else if (distance > retainRadius && image.getAttribute('src')) {
            image.removeAttribute('src');
        }
    });
    if (centerIndex >= readerPages.length - 3) prefetchNextReaderChapter();
}

function getAdaptiveReaderPreloadRadius() {
    if (typeof getAppPreference === 'function' && getAppPreference('low_bandwidth_mode', false)) return 1;
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (connection?.saveData || ['slow-2g', '2g'].includes(connection?.effectiveType)) return 1;
    if (connection?.effectiveType === '3g') return WEBTOON_PRELOAD_RADIUS;
    return 6;
}

function isKnownWideReaderPage(index) {
    const ratio = readerPageAspectRatios.get(`${readerMangaId}|${readerChapterId}|${index}`);
    if (!ratio) return false;
    const parts = ratio.split('/').map(Number);
    return parts.length === 2 && parts[0] / parts[1] > 1.15;
}

function getClassicSpreadStart(index) {
    const safeIndex = Math.max(0, Math.min(Number(index) || 0, Math.max(readerPages.length - 1, 0)));
    if (readerPreferences.spread !== 'double' || isKnownWideReaderPage(safeIndex)) return safeIndex;
    if (readerPreferences.coverSingle && safeIndex === 0) return 0;
    const base = (readerPreferences.coverSingle ? 1 : 0) + readerPreferences.spreadOffset;
    if (safeIndex < base) return safeIndex;
    return base + Math.floor((safeIndex - base) / 2) * 2;
}

function showClassicSpread(index) {
    const start = getClassicSpreadStart(index);
    readerPageIndex = start;
    const visible = new Set([start]);
    if (readerPreferences.spread === 'double' && !isKnownWideReaderPage(start) && start + 1 < readerPages.length && !isKnownWideReaderPage(start + 1)) {
        visible.add(start + 1);
    }
    const wrapper = document.getElementById('pages-wrapper');
    wrapper.querySelectorAll('.manga-page').forEach(image => image.removeAttribute('src'));
    wrapper.innerHTML = '';
    [...visible].forEach(pageIndex => {
        const image = document.createElement('img');
        image.src = readerPages[pageIndex];
        image.className = 'manga-page active';
        image.dataset.index = String(pageIndex);
        image.decoding = 'async';
        image.onerror = () => renderClassicPageFailure(image, pageIndex);
        image.onload = () => {
            if (image.naturalWidth && image.naturalHeight) {
                const key = `${readerMangaId}|${readerChapterId}|${pageIndex}`;
                const ratio = `${image.naturalWidth} / ${image.naturalHeight}`;
                const wasWide = isKnownWideReaderPage(pageIndex);
                rememberReaderPageDimension(key, ratio);
                image.classList.toggle('reader-wide-page', image.naturalWidth / image.naturalHeight > 1.15);
                if (!wasWide && isKnownWideReaderPage(pageIndex) && visible.size > 1) showClassicSpread(pageIndex);
            }
        };
        wrapper.appendChild(image);
    });
    prefetchClassicReaderPages(start);
    applyReaderPreferences();
    document.getElementById('reader-overlay').classList.remove('reader-at-end');
    document.getElementById('end-chapter-card').classList.add('hidden');
    readerAtEndCard = false;
}

function renderClassicPageFailure(image, pageIndex) {
    image.onerror = null;
    image.src = '/static/img/no-page.jpg';
    image.classList.add('reader-page-placeholder');
    showReaderLoadError(`Sayfa ${pageIndex + 1} yüklenemedi`, readerIsOnline
        ? 'Bağlantı veya bölüm kaynağı geçici olarak yanıt vermiyor olabilir.'
        : 'Yerel sayfa dosyası eksik veya okunamıyor olabilir.', { online: readerIsOnline, reason: 'page_error' });
}

function prefetchClassicReaderPages(start) {
    const radius = getAdaptiveReaderPreloadRadius();
    const retainedUrls = new Set(readerPages.slice(Math.max(0, start - 1), Math.min(readerPages.length, start + radius + 1)));
    for (const [url, image] of readerClassicPrefetch) {
        if (!retainedUrls.has(url)) {
            image.src = '';
            readerClassicPrefetch.delete(url);
        }
    }
    for (let index = start + 1; index <= Math.min(readerPages.length - 1, start + radius); index += 1) {
        const url = readerPages[index];
        if (!url || readerClassicPrefetch.has(url)) continue;
        const image = new Image();
        readerClassicPrefetch.set(url, image);
        image.onload = () => {
            rememberReaderPageDimension(`${readerMangaId}|${readerChapterId}|${index}`, `${image.naturalWidth} / ${image.naturalHeight}`);
        };
        image.onerror = () => readerClassicPrefetch.delete(url);
        image.src = url;
    }
    if (start >= readerPages.length - 3) prefetchNextReaderChapter();
}

async function prefetchNextReaderChapter() {
    if (!readerIsOnline || typeof getAppPreference === 'function' && getAppPreference('low_bandwidth_mode', false)) return;
    const chapters = getFilteredChapters();
    const current = chapters.findIndex(item => item.id === readerChapterId);
    const next = current >= 0 ? chapters[current + 1] : null;
    if (!next || readerFallbackPageCache.has(next.id)) return;
    readerFallbackPageCache.set(next.id, null);
    try {
        const pages = await fetchReaderChapterPages(readerMangaId, next.id, { timeoutMs: 10000 });
        if (pages.length) {
            readerFallbackPageCache.set(next.id, pages);
            pages.slice(0, 2).forEach(url => { const image = new Image(); image.src = url; });
        } else {
            readerFallbackPageCache.delete(next.id);
        }
    } catch (_) {
        readerFallbackPageCache.delete(next.id);
    }
}

function currentClassicSpreadSize(index = readerPageIndex) {
    if (readerPreferences.spread !== 'double' || isKnownWideReaderPage(index)) return 1;
    if (readerPreferences.coverSingle && index === 0) return 1;
    return index + 1 < readerPages.length && !isKnownWideReaderPage(index + 1) ? 2 : 1;
}

function syncPageRange(index, totalPages) {
    const range = document.getElementById('reader-page-range');
    if (!range) return;

    const max = Math.max(totalPages - 1, 1);
    const value = Math.max(0, Math.min(index, max));
    range.min = 0;
    range.max = max;
    range.value = value;
    // Bazı WebView sürümleri yalnızca property değişiminde thumb'ı yeniden
    // çizmez; attribute'u da eşitlemek ilk karede doğru konumu garantiler.
    range.setAttribute('value', String(value));
}

function updatePageIndicator() {
    const indicator = document.getElementById('reader-page-indicator');
    const spreadEnd = readerMode === 'classic'
        ? Math.min(readerPageIndex + currentClassicSpreadSize(), readerPages.length)
        : readerPageIndex + 1;
    const pageLabel = spreadEnd > readerPageIndex + 1
        ? `${readerPageIndex + 1}-${spreadEnd}`
        : `${readerPageIndex + 1}`;
    indicator.textContent = `${pageLabel} / ${readerPages.length}`;

    syncPageRange(readerPageIndex, readerPages.length);
    updateReaderBookmarkButton();

    // Update prev/next chapter button states
    const chapterList = getFilteredChapters();
    const currentIdx = chapterList.findIndex(c => c.id === readerChapterId);

    document.getElementById('reader-prev-chap').disabled = (currentIdx === 0);
    document.getElementById('reader-next-chap').disabled = (currentIdx !== -1 && currentIdx === chapterList.length - 1);
}

// Get chapters filtered by the same language as the current chapter
function getFilteredChapters() {
    if (!activeChapters || activeChapters.length === 0) return [];

    const currentChapter = activeChapters.find(c => c.id === readerChapterId);
    const lang = currentChapter ? currentChapter.language : null;

    let list = lang ? activeChapters.filter(c => c.language === lang) : [...activeChapters];

    if (!readerIsOnline) {
        const localManga = libraryData.mangas[readerMangaId] || null;
        const downloadedIds = localManga ? Object.keys(localManga.downloaded_chapters || {}) : [];
        list = list.filter(c => downloadedIds.includes(c.id));
    }

    // Kaynaklar bölüm listesini farklı yönlerde döndürebilir. Okuyucu
    // navigasyonunda solda daima düşük, sağda daima yüksek bölüm olmalı.
    return list
        .map((chapter, originalIndex) => ({
            chapter,
            originalIndex,
            numericChapter: Number.parseFloat(String(chapter.chapter).replace(',', '.'))
        }))
        .sort((a, b) => {
            const aHasNumber = Number.isFinite(a.numericChapter);
            const bHasNumber = Number.isFinite(b.numericChapter);
            if (aHasNumber && bHasNumber && a.numericChapter !== b.numericChapter) {
                return a.numericChapter - b.numericChapter;
            }
            if (aHasNumber !== bHasNumber) return aHasNumber ? -1 : 1;
            return a.originalIndex - b.originalIndex;
        })
        .map(item => item.chapter);
}

// Navigation Controls
function jumpToPage(index, { behavior = 'smooth', save = true } = {}) {
    if (index < 0 || index >= readerPages.length) return;

    readerPageIndex = readerMode === 'classic' ? getClassicSpreadStart(index) : index;
    updatePageIndicator();

    if (readerMode === 'webtoon') {
        updateWebtoonImageWindow(readerPageIndex);
        const img = document.querySelector(`.manga-page[data-index="${readerPageIndex}"]`);
        if (img) {
            img.scrollIntoView({ behavior, block: 'start' });
        }
    } else {
        showClassicSpread(readerPageIndex);

        // Son manga sayfası ile bölüm sonu kartı iki ayrı adımdır.
        document.getElementById('end-chapter-card').classList.add('hidden');
        readerAtEndCard = false;
    }

    cancelAutoNext();
    if (save) queueProgressSave(readerPageIndex, 0, getChapterPercent(readerPageIndex, 0));
}

// Classic Page-by-Page Actions
function classicNextPage() {
    const step = currentClassicSpreadSize();
    if (readerPageIndex + step < readerPages.length) {
        jumpToPage(readerPageIndex + step);
    } else {
        showReaderEndCard();
    }
}

function classicPrevPage() {
    if (readerAtEndCard) {
        cancelAutoNext();
        jumpToPage(getClassicSpreadStart(readerPages.length - 1));
        return;
    }

    if (readerPageIndex > 0) {
        let previous = Math.max(0, readerPageIndex - 1);
        while (previous > 0 && getClassicSpreadStart(previous) === readerPageIndex) previous -= 1;
        jumpToPage(getClassicSpreadStart(previous));
    }
}

function classicLeftAction() {
    classicPrevPage();
}

function classicRightAction() {
    classicNextPage();
}

function getChapterPercent(index, pageOffset = 0) {
    if (!readerPages.length) return 0;
    return Math.max(0, Math.min(1, (Number(index) + Number(pageOffset || 0)) / readerPages.length));
}

function calculateWebtoonProgress() {
    const viewport = document.getElementById('reader-viewport');
    const pages = [...document.querySelectorAll('#pages-wrapper .reader-page-shell')];
    if (!viewport || !pages.length) return { index: readerPageIndex, offset: 0, percent: 0 };

    const marker = viewport.scrollTop + Math.min(viewport.clientHeight * 0.35, 280);
    let index = 0;
    let offset = 0;
    for (let i = 0; i < pages.length; i += 1) {
        const page = pages[i];
        const top = page.offsetTop;
        const height = Math.max(page.offsetHeight, 1);
        if (marker >= top) {
            index = i;
            offset = Math.max(0, Math.min(0.9999, (marker - top) / height));
        }
        if (marker < top + height) break;
    }
    const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 12;
    return {
        index,
        offset: atBottom ? 1 : offset,
        percent: atBottom ? 1 : getChapterPercent(index, offset),
        atBottom,
    };
}

function updatePreciseWebtoonProgress(save = true) {
    if (readerMode !== 'webtoon' || !readerPages.length) return;
    const progress = calculateWebtoonProgress();
    readerPageIndex = progress.index;
    updatePageIndicator();
    if (save && _readerProgressReady) saveProgressThrottled(progress.index, progress.offset, progress.percent);
    if (_readerProgressReady && progress.atBottom) scheduleAutoNext();
    else if (!progress.atBottom) cancelAutoNext();
}

function handleWebtoonScroll() {
    updatePreciseWebtoonProgress(true);
    showReaderControls();
    resetControlsTimeout();
}

function restoreReaderProgress(index, offset = 0, percent = 0) {
    if (!readerPages.length) return;
    const safeIndex = Math.max(0, Math.min(Number(index) || 0, readerPages.length - 1));
    if (readerMode === 'classic') {
        jumpToPage(safeIndex, { behavior: 'auto', save: false });
        _readerProgressReady = true;
        return;
    }

    const image = document.querySelector(`#pages-wrapper .manga-page[data-index="${safeIndex}"]`);
    const viewport = document.getElementById('reader-viewport');
    if (!image || !viewport) return;
    const restore = () => {
        const fallbackOffset = percent > 0
            ? Math.max(0, Math.min(0.9999, percent * readerPages.length - safeIndex))
            : 0;
        const pageOffset = Math.max(0, Math.min(0.9999, Number(offset) || fallbackOffset));
        const markerOffset = Math.min(viewport.clientHeight * 0.35, 280);
        viewport.scrollTop = Math.max(0, image.offsetTop + image.offsetHeight * pageOffset - markerOffset);
        readerPageIndex = safeIndex;
        updatePageIndicator();
        _readerProgressReady = true;
    };
    if (image.complete) requestAnimationFrame(restore);
    else image.addEventListener('load', () => requestAnimationFrame(restore), { once: true });
}

function hasNextChapter() {
    const list = getFilteredChapters();
    const currentIdx = list.findIndex(chapter => chapter.id === readerChapterId);
    return currentIdx !== -1 && currentIdx < list.length - 1;
}

function cancelAutoNext() {
    if (_readerAutoNextTimer) clearInterval(_readerAutoNextTimer);
    _readerAutoNextTimer = null;
    _readerAutoNextRemaining = 0;
    const message = document.getElementById('end-chapter-message');
    if (message) message.textContent = 'Bu bölümün sonuna geldiniz.';
}

function scheduleAutoNext() {
    if (!readerPreferences.autoNext || !hasNextChapter() || _readerAutoNextTimer) return;
    _readerAutoNextRemaining = 5;
    const message = document.getElementById('end-chapter-message');
    const updateMessage = () => {
        if (message) message.textContent = `Sonraki bölüm ${_readerAutoNextRemaining} saniye içinde açılacak.`;
    };
    updateMessage();
    _readerAutoNextTimer = setInterval(async () => {
        _readerAutoNextRemaining -= 1;
        updateMessage();
        if (_readerAutoNextRemaining <= 0) {
            cancelAutoNext();
            await nextChapter();
        }
    }, 1000);
}

function showReaderEndCard() {
    readerAtEndCard = true;
    document.getElementById('reader-overlay').classList.add('reader-at-end');
    document.getElementById('end-chapter-card').classList.remove('hidden');
    queueProgressSave(readerPages.length - 1, 1, 1);
    scheduleAutoNext();
}

function currentReaderBookmark() {
    return readerBookmarks.find(item => item.chapter_id === readerChapterId && Number(item.page_index) === readerPageIndex) || null;
}

function updateReaderBookmarkButton() {
    const button = document.getElementById('reader-bookmark-btn');
    if (!button) return;
    const active = Boolean(currentReaderBookmark());
    button.classList.toggle('active', active);
    button.title = active ? 'Bu sayfadaki yer imini kaldır' : 'Bu sayfaya yer imi ekle';
    button.setAttribute('aria-label', button.title);
    const icon = button.querySelector('i');
    if (icon) icon.className = active ? 'fa-solid fa-bookmark' : 'fa-regular fa-bookmark';
}

function renderReaderBookmarks() {
    const list = document.getElementById('reader-bookmarks-list');
    const count = document.getElementById('reader-bookmarks-count');
    if (!list || !count) return;
    list.replaceChildren();
    count.textContent = String(readerBookmarks.length);
    if (!readerBookmarks.length) {
        const empty = document.createElement('p'); empty.textContent = 'Henüz yer imi yok.'; list.appendChild(empty);
        updateReaderBookmarkButton();
        return;
    }
    [...readerBookmarks].reverse().forEach(bookmark => {
        const row = document.createElement('div'); row.className = 'reader-bookmark-row';
        const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'reader-bookmark-jump';
        jump.textContent = bookmark.label || `Bölüm ${bookmark.chapter_num || '?'} · Sayfa ${Number(bookmark.page_index) + 1}`;
        const detail = document.createElement('small'); detail.textContent = bookmark.chapter_title || `Sayfa ${Number(bookmark.page_index) + 1}`; jump.appendChild(detail);
        jump.addEventListener('click', async () => {
            if (bookmark.chapter_id !== readerChapterId) await startReading(readerMangaId, bookmark.chapter_id, readerIsOnline);
            jumpToPage(Number(bookmark.page_index), { behavior: 'auto' });
            toggleReaderSettings(false);
        });
        const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'reader-bookmark-edit'; edit.title = 'Yer imi notunu düzenle'; edit.setAttribute('aria-label', 'Yer imi notunu düzenle');
        const editIcon = document.createElement('i'); editIcon.className = 'fa-solid fa-pen'; edit.appendChild(editIcon);
        edit.addEventListener('click', () => editReaderBookmark(bookmark));
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'reader-bookmark-remove'; remove.title = 'Yer imini kaldır'; remove.setAttribute('aria-label', 'Yer imini kaldır');
        const icon = document.createElement('i'); icon.className = 'fa-solid fa-xmark'; remove.appendChild(icon);
        remove.addEventListener('click', () => removeReaderBookmark(bookmark.chapter_id, bookmark.page_index));
        row.append(jump, edit, remove); list.appendChild(row);
    });
    updateReaderBookmarkButton();
}

async function toggleCurrentPageBookmark() {
    if (!readerMangaId || !readerChapterId || !readerPages.length) return;
    const existing = currentReaderBookmark();
    if (existing) return removeReaderBookmark(existing.chapter_id, existing.page_index);
    const chapter = activeChapters.find(item => item.id === readerChapterId) || {};
    try {
        const response = await fetch(`/api/library/${encodeURIComponent(readerMangaId)}/bookmarks`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chapter_id: readerChapterId, page_index: readerPageIndex, chapter_num: String(chapter.chapter || ''), chapter_title: chapter.title || '', label: '' }),
        });
        if (!response.ok) throw new Error('Yer imi kaydedilemedi');
        const result = await response.json(); readerBookmarks = result.bookmarks || [];
        if (result.manga) libraryData.mangas[readerMangaId] = result.manga;
        renderReaderBookmarks(); showToast('Sayfaya yer imi eklendi.', 'success');
    } catch (_) { showToast('Yer imi kaydedilemedi.', 'error'); }
}

async function removeReaderBookmark(chapterId, pageIndex) {
    try {
        const response = await fetch(`/api/library/${encodeURIComponent(readerMangaId)}/bookmarks/${encodeURIComponent(chapterId)}/${Number(pageIndex)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('Yer imi kaldırılamadı');
        const result = await response.json(); readerBookmarks = result.bookmarks || [];
        if (result.manga) libraryData.mangas[readerMangaId] = result.manga;
        renderReaderBookmarks(); showToast('Yer imi kaldırıldı.', 'success');
    } catch (_) { showToast('Yer imi kaldırılamadı.', 'error'); }
}

async function editReaderBookmark(bookmark) {
    const label = window.prompt('Bu yer imi için kısa bir not yaz:', bookmark.label || '');
    if (label === null) return;
    try {
        const response = await fetch(`/api/library/${encodeURIComponent(readerMangaId)}/bookmarks`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...bookmark, label: String(label).trim().slice(0, 120) }),
        });
        if (!response.ok) throw new Error('Yer imi notu kaydedilemedi');
        const result = await response.json(); readerBookmarks = result.bookmarks || [];
        if (result.manga) libraryData.mangas[readerMangaId] = result.manga;
        renderReaderBookmarks();
    } catch (_) { showToast('Yer imi notu kaydedilemedi.', 'error'); }
}

function toggleReaderFocusMode(force) {
    readerFocusMode = typeof force === 'boolean' ? force : !readerFocusMode;
    const overlay = document.getElementById('reader-overlay');
    overlay.classList.toggle('reader-focus-mode', readerFocusMode);
    document.getElementById('reader-focus-btn')?.classList.toggle('active', readerFocusMode);
}

async function toggleReaderFullscreen() {
    try {
        if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
        else await document.exitFullscreen();
    } catch (_) {
        showToast('Bu WebView tam ekran modunu desteklemiyor.', 'info');
    }
}

document.addEventListener('fullscreenchange', () => {
    const button = document.getElementById('reader-fullscreen-btn');
    button?.classList.toggle('active', Boolean(document.fullscreenElement));
    const icon = button?.querySelector('i');
    if (icon) icon.className = document.fullscreenElement ? 'fa-solid fa-compress' : 'fa-solid fa-expand';
});

// Chapter Switching
async function nextChapter() {
    const list = getFilteredChapters();
    const currentIdx = list.findIndex(c => c.id === readerChapterId);

    if (currentIdx !== -1 && currentIdx < list.length - 1) {
        await startReading(readerMangaId, list[currentIdx + 1].id, readerIsOnline);
    } else if (currentIdx === -1 && list.length > 0) {
        await startReading(readerMangaId, list[0].id, readerIsOnline);
    } else {
        showToast("Son bölümdesiniz.", "info");
    }
}

async function prevChapter() {
    const list = getFilteredChapters();
    const currentIdx = list.findIndex(c => c.id === readerChapterId);

    if (currentIdx > 0) {
        await startReading(readerMangaId, list[currentIdx - 1].id, readerIsOnline);
    } else if (currentIdx === -1 && list.length > 0) {
        await startReading(readerMangaId, list[list.length - 1].id, readerIsOnline);
    } else {
        showToast("İlk bölümdesiniz.", "info");
    }
}

function releaseReaderPageResources() {
    const wrapper = document.getElementById('pages-wrapper');
    const viewport = document.getElementById('reader-viewport');
    const endCard = document.getElementById('end-chapter-card');
    if (endCard && wrapper && endCard.parentElement === wrapper) viewport?.appendChild(endCard);
    wrapper?.querySelectorAll('img').forEach(image => {
        const source = image.currentSrc || image.src || '';
        image.removeAttribute('src');
        image.removeAttribute('srcset');
        if (source.startsWith('blob:')) URL.revokeObjectURL(source);
    });
    if (wrapper) wrapper.innerHTML = '';
    endCard?.classList.add('hidden');
    readerPages = [];
    readerFallbackPageCache.clear();
    readerClassicPrefetch.clear();
    hideReaderLoadError();
    readerPageIndex = 0;
    syncPageRange(0, 0);
}

async function exitReader() {
    if (readerMode === 'webtoon' && readerPages.length) updatePreciseWebtoonProgress(true);
    await flushReaderProgress({ keepalive: true });
    flushReaderPageDimensions();
    cancelAutoNext();
    toggleReaderSettings(false);
    closeReaderShortcuts();
    toggleReaderFocusMode(false);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    // Cancel any in-progress fetch
    if (_readerFetchController) {
        _readerFetchController.abort();
        _readerFetchController = null;
    }

    if (webtoonObserver) {
        webtoonObserver.disconnect();
        webtoonObserver = null;
    }

    document.getElementById('reader-overlay').classList.remove('active');
    document.onkeydown = null;
    releaseReaderPageResources();

    loadLibrary();
}

// Save Progress Backend Request
async function saveProgress(index, pageOffset = 0, chapterPercent = getChapterPercent(index, pageOffset), { keepalive = false } = {}) {
    try {
        const chapter = activeChapters.find(item => item.id === readerChapterId) || {};
        const chapterSource = allFetchedSources.find(source =>
            source.chapters.some(item => item.id === readerChapterId)
        );
        const response = await fetch('/api/progress', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                manga_id: readerMangaId,
                chapter_id: readerChapterId,
                page_index: index,
                page_offset: Math.max(0, Math.min(1, Number(pageOffset) || 0)),
                chapter_percent: Math.max(0, Math.min(1, Number(chapterPercent) || 0)),
                manga_title: activeManga?.title || 'Bilinmeyen Manga',
                description: activeManga?.description || '',
                cover_url: activeManga?.cover_url || '',
                status: activeManga?.status || 'ongoing',
                chapter_num: String(chapter.chapter || ''),
                chapter_title: chapter.title || '',
                source_id: chapterSource?.id || readerMangaId,
                language: chapter.language === 'en' ? 'en' : 'tr',
                online: Boolean(readerIsOnline)
            }),
            keepalive,
        });
        if (response.ok) {
            const result = await response.json();
            if (result.manga) {
                libraryData.mangas[readerMangaId] = result.manga;
                if (typeof cacheLibrarySnapshot === 'function') cacheLibrarySnapshot(libraryData);
                if (typeof scheduleKnownChapterSync === 'function') {
                    scheduleKnownChapterSync(
                        readerMangaId,
                        allFetchedSources.flatMap(source => source.chapters || [])
                    );
                }
            }
        }
    } catch (e) {
        console.error("Progress save failed", e);
    }
}

// Throttle progress savings for scrolling
let progressSaveTimeout = null;
function queueProgressSave(index, pageOffset = 0, chapterPercent = getChapterPercent(index, pageOffset), options = {}) {
    _progressSavePromise = _progressSavePromise
        .catch(() => {})
        .then(() => saveProgress(index, pageOffset, chapterPercent, options));
    return _progressSavePromise;
}

function saveProgressThrottled(index, pageOffset = 0, chapterPercent = getChapterPercent(index, pageOffset)) {
    _pendingProgress = { index, pageOffset, chapterPercent };
    if (progressSaveTimeout) return;
    progressSaveTimeout = setTimeout(() => {
        const pending = _pendingProgress;
        _pendingProgress = null;
        progressSaveTimeout = null;
        if (pending) queueProgressSave(pending.index, pending.pageOffset, pending.chapterPercent);
    }, 700);
}

async function flushReaderProgress({ keepalive = false } = {}) {
    if (progressSaveTimeout) clearTimeout(progressSaveTimeout);
    progressSaveTimeout = null;
    const pending = _pendingProgress;
    _pendingProgress = null;
    if (pending) queueProgressSave(pending.index, pending.pageOffset, pending.chapterPercent, { keepalive });
    await _progressSavePromise.catch(() => {});
}

// UI Controls Auto-hide
function setupControlsAutohide() {
    const overlay = document.getElementById('reader-overlay');

    overlay.onmousemove = () => {
        showReaderControls();
        resetControlsTimeout();
    };

    overlay.onclick = (e) => {
        if (!e.target.closest('.reader-header') && !e.target.closest('.reader-footer') && !e.target.closest('.classic-hitbox') && !e.target.closest('.reader-settings-panel') && !e.target.closest('.reader-shortcuts-modal')) {
            if (overlay.classList.contains('controls-hidden')) {
                showReaderControls();
                resetControlsTimeout();
            } else {
                hideReaderControls();
            }
        }
    };

    resetControlsTimeout();
}

function resetControlsTimeout() {
    if (controlsTimeout) clearTimeout(controlsTimeout);
    controlsTimeout = setTimeout(hideReaderControls, 3000);
}

function showReaderControls() {
    document.getElementById('reader-overlay').classList.remove('controls-hidden');
}

function hideReaderControls() {
    document.getElementById('reader-overlay').classList.add('controls-hidden');
}

function handleReaderWheel(e) {
    const overlay = document.getElementById('reader-overlay');
    if (readerMode !== 'classic' || !overlay.classList.contains('active')) return;

    let delta = 0;
    if (typeof e.deltaY === 'number' && e.deltaY !== 0) {
        delta = e.deltaY;
    } else if (typeof e.wheelDelta === 'number' && e.wheelDelta !== 0) {
        // Legacy Chromium/WebView: wheelDelta yukarı yönde pozitiftir.
        delta = -e.wheelDelta;
    } else if (typeof e.detail === 'number' && e.detail !== 0) {
        delta = e.detail;
    } else if (typeof e.deltaX === 'number') {
        delta = e.deltaX;
    }
    if (delta === 0) return;

    e.preventDefault();
    const direction = delta > 0 ? 1 : -1;
    const now = performance.now();
    if (direction === _lastClassicWheelDirection && now - _lastClassicWheelAt < 120) return;
    _lastClassicWheelAt = now;
    _lastClassicWheelDirection = direction;

    if (direction > 0) {
        classicNextPage();
    } else {
        classicPrevPage();
    }

    showReaderControls();
    resetControlsTimeout();
}

function handleReaderKeydown(e) {
    const overlay = document.getElementById('reader-overlay');
    if (!overlay.classList.contains('active')) return;

    const target = e.target && typeof e.target.closest === 'function' ? e.target : null;
    if (target && target.closest('input, select, textarea')) return;

    if (e.key === 'Escape') {
        e.preventDefault();
        const shortcuts = document.getElementById('reader-shortcuts-modal');
        const settings = document.getElementById('reader-settings-panel');
        if (shortcuts?.classList.contains('active')) {
            closeReaderShortcuts();
            return;
        }
        if (settings?.classList.contains('active')) {
            toggleReaderSettings(false);
            return;
        }
        exitReader();
        return;
    }

    const key = e.key.toLowerCase();
    if (key === '?' || (key === '/' && e.shiftKey)) {
        e.preventDefault();
        openReaderShortcuts();
        return;
    }
    if (key === 'p') {
        e.preventDefault();
        toggleReaderSettings();
        return;
    }
    if (key === 'b') {
        e.preventDefault();
        toggleCurrentPageBookmark();
        return;
    }
    if (e.key === 'F11') {
        e.preventDefault();
        toggleReaderFullscreen();
        return;
    }
    if (key === 'm') {
        e.preventDefault();
        changeReaderMode(readerMode === 'classic' ? 'webtoon' : 'classic');
        return;
    }
    if (key === 'f') {
        e.preventDefault();
        setReaderPreference('fit', readerPreferences.fit === 'page' ? 'width' : 'page');
        return;
    }
    if (key === '+' || key === '=') {
        e.preventDefault();
        adjustReaderZoom(10);
        return;
    }
    if (key === '-') {
        e.preventDefault();
        adjustReaderZoom(-10);
        return;
    }
    if (key === '0') {
        e.preventDefault();
        setReaderPreference('zoom', 100);
        return;
    }
    if (readerMode === 'classic') {
        if (key === 'arrowleft' || key === 'a') {
            e.preventDefault();
            classicLeftAction();
        } else if (key === 'arrowright' || key === 'd') {
            e.preventDefault();
            classicRightAction();
        } else {
            return;
        }
    } else {
        const viewport = document.getElementById('reader-viewport');
        if (key === 'arrowdown') {
            e.preventDefault();
            viewport.scrollBy({ top: 300, behavior: 'smooth' });
        } else if (key === 'arrowup') {
            e.preventDefault();
            viewport.scrollBy({ top: -300, behavior: 'smooth' });
        } else {
            return;
        }
    }

    showReaderControls();
    resetControlsTimeout();
}

function setupReaderInputControls() {
    if (_readerGlobalControlsBound) return;

    // Window capture, mouse olayı hangi görsel/ok katmanında başlarsa başlasın
    // okuyucuya ulaşmasını sağlar. passive:false preventDefault için gereklidir.
    window.addEventListener('wheel', handleReaderWheel, {
        capture: true,
        passive: false
    });
    window.addEventListener('mousewheel', handleReaderWheel, {
        capture: true,
        passive: false
    });
    window.addEventListener('keydown', handleReaderKeydown, true);
    _readerGlobalControlsBound = true;
}

function setupKeyboardControls() {
    setupReaderInputControls();
}

// reader.js sayfanın sonunda yüklendiği için overlay bu noktada hazırdır.
document.getElementById('reader-mode').value = readerMode;
setupReaderInputControls();
