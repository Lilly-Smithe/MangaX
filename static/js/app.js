// Global App State
const APP_LANGUAGE_STORAGE_KEY = 'mangax-preferred-language-v1';
const SETTINGS_CATEGORY_STORAGE_KEY = 'mangax-settings-category-v1';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'mangax-sidebar-collapsed-v1';
const LIBRARY_SNAPSHOT_STORAGE_KEY = 'mangax-library-snapshot-v1';
const LIBRARY_SNAPSHOT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const mangaxStartupStartedAt = performance.now();
const mangaxStartupMarks = { process_started: 0 };
let initialLibraryRequestCompleted = false;

function markStartupMilestone(name) {
    if (!name || Object.prototype.hasOwnProperty.call(mangaxStartupMarks, name)) return;
    mangaxStartupMarks[name] = Math.max(0, performance.now() - mangaxStartupStartedAt);
    window.mangaxStartupTimeline = { ...mangaxStartupMarks };
}

function activateDeferredStyles() {
    document.querySelectorAll('link[data-deferred-style]').forEach(link => {
        link.media = 'all';
        link.removeAttribute('data-deferred-style');
    });
}

function runWhenAppIdle(callback, timeout = 1200) {
    if (typeof requestIdleCallback === 'function') {
        return requestIdleCallback(callback, { timeout });
    }
    return setTimeout(callback, Math.min(timeout, 800));
}

function createLibrarySnapshot(data) {
    const mangas = Object.fromEntries(Object.entries(data?.mangas || {}).map(([id, manga]) => {
        const downloaded = Object.fromEntries(Object.entries(manga.downloaded_chapters || {}).map(([chapterId, chapter]) => [
            chapterId,
            { ...chapter, pages: [] },
        ]));
        return [id, { ...manga, downloaded_chapters: downloaded }];
    }));
    return { mangas };
}

function cacheLibrarySnapshot(data) {
    try {
        localStorage.setItem(LIBRARY_SNAPSHOT_STORAGE_KEY, JSON.stringify({
            savedAt: Date.now(),
            data: createLibrarySnapshot(data),
        }));
    } catch (_) { /* büyük kütüphanelerde canlı veri kullanılmaya devam eder */ }
}

function hydrateLibraryFromSnapshot() {
    try {
        const cached = JSON.parse(localStorage.getItem(LIBRARY_SNAPSHOT_STORAGE_KEY) || 'null');
        if (!cached?.data?.mangas || Date.now() - Number(cached.savedAt || 0) > LIBRARY_SNAPSHOT_MAX_AGE_MS) return false;
        renderLibrarySnapshot(cached.data);
        return true;
    } catch (_) {
        return false;
    }
}

function getPreferredAppLanguage() {
    try {
        return localStorage.getItem(APP_LANGUAGE_STORAGE_KEY) === 'en' ? 'en' : 'tr';
    } catch (_) {
        return 'tr';
    }
}

function getSavedSidebarCollapsed() {
    try {
        return localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true';
    } catch (_) {
        return false;
    }
}

function setSidebarCollapsed(collapsed, { persist = true } = {}) {
    const sidebar = document.getElementById('app-sidebar');
    const button = document.getElementById('sidebar-collapse-btn');
    if (!sidebar || !button) return;

    const isCollapsed = Boolean(collapsed);
    sidebar.classList.toggle('sidebar-collapsed', isCollapsed);
    button.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
    button.setAttribute('aria-label', isCollapsed ? 'Sol menüyü genişlet' : 'Sol menüyü daralt');
    button.title = isCollapsed ? 'Menüyü genişlet' : 'Menüyü daralt';
    const icon = button.querySelector('i');
    if (icon) icon.className = `fa-solid ${isCollapsed ? 'fa-angles-right' : 'fa-angles-left'}`;

    if (persist) {
        try {
            localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, isCollapsed ? 'true' : 'false');
        } catch (_) { /* tercih bu oturumda çalışmaya devam eder */ }
    }
}

function toggleSidebarCollapsed() {
    const sidebar = document.getElementById('app-sidebar');
    if (!sidebar) return;
    setSidebarCollapsed(!sidebar.classList.contains('sidebar-collapsed'));
}

let currentTab = 'library';
let activeManga = null;       // Store current viewed manga details
let activeChapters = [];      // Store chapter list of active manga
let activeLang = getPreferredAppLanguage(); // Default chapter list language
let libraryData = { mangas: {} };
let downloadStatusInterval = null;
let searchResults = {};       // Store search results for group lookup
let allFetchedSources = [];   // Store pre-loaded sources for active manga
let activeDiscoverLang = getPreferredAppLanguage(); // Global state for home page language filter
let activeLibraryView = 'continue';
let activeLibraryStatus = 'all';
let activeLibraryCollection = '';
let librarySortOrder = 'updated_desc';
let libraryCatalogView = getSavedLibraryCatalogView();
let librarySelectionMode = false;
let selectedLibraryMangaIds = new Set();
let activeLibraryEditorId = '';
const knownChapterSyncTimers = new Map();
const pendingKnownChapters = new Map();
let detailOnlineMode = false; // Controls whether library manga is viewed online or offline-first
let chapterDownloadStatus = {}; // Modal satırlarındaki canlı indirme durumları
const optimisticDownloadIds = new Set();
const ignoredCompletedDownloadIds = new Set();
let downloadStatusRequestInFlight = false;
let appConfirmResolver = null;
let appConfirmPreviousFocus = null;
let mangaNewsItems = [];
let activeNewsIndex = 0;
let mangaNewsRotationTimer = null;
let mangaNewsRequestCounter = 0;
const sourceHealthResults = new Map();
let sourceBatchTestRunning = false;
const discoverShuffleSeed = getDiscoverShuffleSeed();
let discoverScreenLoaded = false;

function getSavedLibraryCatalogView() {
    try {
        return localStorage.getItem('mangax-library-view') === 'list' ? 'list' : 'cover';
    } catch (_) {
        return 'cover';
    }
}

function getDiscoverShuffleSeed() {
    const storageKey = 'mangaxDiscoverShuffleSeed';
    try {
        const existing = sessionStorage.getItem(storageKey);
        if (existing) return existing;

        const values = new Uint32Array(1);
        const seed = globalThis.crypto?.getRandomValues
            ? globalThis.crypto.getRandomValues(values)[0].toString(16)
            : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
        sessionStorage.setItem(storageKey, seed);
        return seed;
    } catch (_error) {
        return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
    }
}

function shuffleDiscoverItems(items, rowKey) {
    const shuffled = [...items];
    let state = 2166136261;
    const key = `${discoverShuffleSeed}:${activeDiscoverLang}:${rowKey}`;
    for (let index = 0; index < key.length; index += 1) {
        state ^= key.charCodeAt(index);
        state = Math.imul(state, 16777619) >>> 0;
    }

    for (let index = shuffled.length - 1; index > 0; index -= 1) {
        state ^= state << 13;
        state ^= state >>> 17;
        state ^= state << 5;
        const swapIndex = (state >>> 0) % (index + 1);
        [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
    }
    return shuffled;
}

// Chapter filtering & sorting state
let chapterSearchQuery = "";
let chapterSortOrder = "asc";
let activeGroup = "";
let detailRequestCounter = 0; // Request ID tracker to prevent UI race conditions

// Initialization
if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', () => {
    markStartupMilestone('dom_ready');
    setSidebarCollapsed(getSavedSidebarCollapsed(), { persist: false });
    setupTabNavigation();
    setPreferredAppLanguage(activeDiscoverLang, { render: false });
    if (typeof configureLibraryEditionLayout === 'function') configureLibraryEditionLayout();
    const restoredLibrarySnapshot = hydrateLibraryFromSnapshot();
    loadLibrary({ silent: restoredLibrarySnapshot });
    if (typeof syncMangaNewsSetting === 'function') syncMangaNewsSetting();
    setTimeout(() => runWhenAppIdle(() => {
        if (typeof startDownloadStatusPolling === 'function') startDownloadStatusPolling();
        if (typeof scheduleStartupUpdateCheck === 'function') scheduleStartupUpdateCheck();
        fetch('/api/library/maintenance', { method: 'POST' }).catch(error => {
            console.warn('Kütüphane arka plan bakımı başlatılamadı:', error);
        });
    }, 2000), 2500);
    requestAnimationFrame(() => {
        markStartupMilestone('first_frame_ready');
        activateDeferredStyles();
    });
    
    // NSFW toggle switch durumunu güncelle
    const toggle = document.getElementById('settings-nsfw-toggle');
    if (toggle) {
        toggle.checked = localStorage.getItem('show18Plus') === 'true';
    }
    if (typeof loadDownloadCompressionSetting === 'function') loadDownloadCompressionSetting();
    
    // Close modal when clicking background overlay
    const modalBg = document.getElementById('modal-bg');
    if (modalBg) {
        modalBg.addEventListener('click', closeDetailsModal);
    }

    document.addEventListener('keydown', event => {
        const libraryEditor = document.getElementById('library-editor');
        if (event.key === 'Escape' && libraryEditor?.classList.contains('active')) {
            event.preventDefault();
            closeLibraryEditor();
            return;
        }
        const confirmModal = document.getElementById('app-confirm');
        if (!confirmModal || !confirmModal.classList.contains('active')) return;
        const cancelButton = document.getElementById('app-confirm-cancel');
        const acceptButton = document.getElementById('app-confirm-accept');
        if (event.key === 'Escape') {
            event.preventDefault();
            resolveAppConfirm(false);
        } else if (event.key === 'Enter') {
            if (document.activeElement === cancelButton) return;
            event.preventDefault();
            resolveAppConfirm(true);
        } else if (event.key === 'Tab') {
            if (event.shiftKey && document.activeElement === cancelButton) {
                event.preventDefault();
                acceptButton.focus();
            } else if (!event.shiftKey && document.activeElement === acceptButton) {
                event.preventDefault();
                cancelButton.focus();
            }
        }
    });
});

function showAppConfirm({
    title = 'İşlemi Onayla',
    message = '',
    confirmText = 'Onayla',
    cancelText = 'Vazgeç',
    variant = 'default',
    icon = 'fa-circle-question'
} = {}) {
    const modal = document.getElementById('app-confirm');
    if (!modal) return Promise.resolve(false);

    if (appConfirmResolver) {
        appConfirmResolver(false);
        appConfirmResolver = null;
    }

    document.getElementById('app-confirm-title').textContent = title;
    document.getElementById('app-confirm-message').textContent = message;
    document.getElementById('app-confirm-cancel').textContent = cancelText;
    const acceptButton = document.getElementById('app-confirm-accept');
    acceptButton.textContent = confirmText;
    document.querySelector('#app-confirm-icon i').className = `fa-solid ${icon}`;

    modal.classList.toggle('danger', variant === 'danger');
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    appConfirmPreviousFocus = document.activeElement;

    requestAnimationFrame(() => acceptButton.focus());
    return new Promise(resolve => {
        appConfirmResolver = resolve;
    });
}

function resolveAppConfirm(accepted) {
    const modal = document.getElementById('app-confirm');
    if (!modal || !modal.classList.contains('active')) return;

    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    const resolver = appConfirmResolver;
    appConfirmResolver = null;
    if (resolver) resolver(Boolean(accepted));

    if (appConfirmPreviousFocus && typeof appConfirmPreviousFocus.focus === 'function') {
        appConfirmPreviousFocus.focus();
    }
    appConfirmPreviousFocus = null;
}

// Toast System
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let iconClass = 'fa-circle-info';
    if (type === 'success') iconClass = 'fa-circle-check';
    if (type === 'error') iconClass = 'fa-circle-exclamation';
    
    const { element, icon } = window.MangaXSafeDOM;
    toast.append(
        icon(`fa-solid ${iconClass}`),
        element('span', { text: message }),
    );
    
    container.appendChild(toast);
    
    // Remove toast after animation finishes
    setTimeout(() => {
        toast.style.animation = 'slideInRight 0.3s ease-in reverse forwards';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Navigation Tabs
function setupTabNavigation() {
    const navButtons = document.querySelectorAll('.nav-btn');
    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.getAttribute('data-tab');
            switchTab(tabId);
        });
    });
}

function switchTab(tabId, { resetDiscover = true } = {}) {
    if (['browse', 'notifications', 'extensions'].includes(tabId) && document.body?.dataset.githubExtensionsAvailable !== 'true') {
        tabId = 'library';
    }
    currentTab = tabId;
    if (tabId !== 'browse' && typeof cancelDiscoverRequests === 'function') cancelDiscoverRequests();
    
    // Update active nav button
    document.querySelectorAll('.nav-btn').forEach(btn => {
        if (btn.getAttribute('data-tab') === tabId) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
    
    // Update active panel
    document.querySelectorAll('.tab-panel').forEach(panel => {
        if (panel.id === `${tabId}-tab`) {
            panel.classList.add('active');
        } else {
            panel.classList.remove('active');
        }
    });

    if (tabId === 'library') {
        loadLibrary();
    }
    if (tabId === 'browse') {
        discoverScreenLoaded = true;
        if (typeof prepareDiscoverSourceRuntime === 'function') prepareDiscoverSourceRuntime();
        if (resetDiscover && typeof resetDiscoverState === 'function') {
            resetDiscoverState({ reload: true });
        } else if (typeof resetDiscoverState !== 'function' && !document.querySelector('#browse-grid .manga-card')) {
            loadPopular();
        }
        if (isMangaNewsEnabled() && !mangaNewsItems.length) loadMangaNews();
    }
    if (tabId === 'settings') {
        // NSFW toggle switch durumunu güncelle
        const toggle = document.getElementById('settings-nsfw-toggle');
        if (toggle) {
            toggle.checked = localStorage.getItem('show18Plus') === 'true';
        }
        if (typeof syncMangaNewsSetting === 'function') syncMangaNewsSetting();
        if (typeof loadDownloadCompressionSetting === 'function') loadDownloadCompressionSetting();
        if (typeof loadBackupOverview === 'function') loadBackupOverview();
        loadSettingsCategory();
    }
}


// Feature implementations are loaded from the screen modules below.
