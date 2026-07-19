let startupExperienceRequest = null;
let activeReleaseNotesVersion = '';
let releaseNotesPreviousFocus = null;

function legacyOnboardingCompleted() {
    try {
        return ['mangax-onboarding-completed-v1', 'mangax-onboarding-completed-v2', 'mangax-reader-onboarding-completed-v1']
            .some(key => localStorage.getItem(key) === 'true');
    } catch (_) {
        return false;
    }
}

async function loadStartupExperience() {
    if (startupExperienceRequest) return startupExperienceRequest;
    const legacy = legacyOnboardingCompleted();
    startupExperienceRequest = (async () => {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 6000);
        try {
            const response = await fetch(`/api/preferences/startup?legacy_onboarding_completed=${legacy ? 'true' : 'false'}`, {
                cache: 'no-store',
                signal: controller.signal,
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
            return data;
        } catch (error) {
            console.warn('Başlangıç durumu alınamadı:', error);
            return {
                onboarding_completed: legacy,
                show_onboarding: !legacy,
                show_release_notes: false,
                release_notes: null,
            };
        } finally {
            clearTimeout(timeout);
        }
    })();
    return startupExperienceRequest;
}

async function persistOnboardingCompleted() {
    try {
        await fetch('/api/preferences/onboarding/complete', { method: 'POST', cache: 'no-store' });
    } catch (error) {
        console.warn('Başlangıç tamamlanma durumu kaydedilemedi:', error);
    }
}

async function applyStartupExperience(openOnboardingCallback) {
    const state = await loadStartupExperience();
    if (state.show_onboarding) {
        openOnboardingCallback?.();
        return state;
    }
    if (state.show_release_notes && state.release_notes) {
        openReleaseNotes(state.release_notes);
    }
    return state;
}

function releaseNotesFocusableElements() {
    const overlay = document.getElementById('release-notes-overlay');
    return overlay ? [...overlay.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')] : [];
}

function openReleaseNotes(notes) {
    const overlay = document.getElementById('release-notes-overlay');
    const title = document.getElementById('release-notes-title');
    const list = document.getElementById('release-notes-list');
    const items = Array.isArray(notes?.items) ? notes.items.filter(Boolean) : [];
    if (!overlay || !title || !list || !notes?.version || !items.length) return false;
    activeReleaseNotesVersion = String(notes.version);
    releaseNotesPreviousFocus = document.activeElement;
    title.textContent = `MangaX ${activeReleaseNotesVersion} — Neler geliştirildi?`;
    list.innerHTML = items.map(item => `<li><i class="fa-solid fa-circle-check"></i><span>${escapeHtml(String(item))}</span></li>`).join('');
    overlay.classList.add('active');
    overlay.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => document.getElementById('release-notes-close')?.focus());
    return true;
}

async function closeReleaseNotes() {
    const overlay = document.getElementById('release-notes-overlay');
    if (!overlay?.classList.contains('active')) return;
    overlay.classList.remove('active');
    overlay.setAttribute('aria-hidden', 'true');
    const version = activeReleaseNotesVersion;
    activeReleaseNotesVersion = '';
    releaseNotesPreviousFocus?.focus?.();
    releaseNotesPreviousFocus = null;
    if (!version) return;
    try {
        await fetch('/api/preferences/release-notes/seen', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version }),
        });
    } catch (error) {
        console.warn('Sürüm notu durumu kaydedilemedi:', error);
    }
}

function handleReleaseNotesKeydown(event) {
    const overlay = document.getElementById('release-notes-overlay');
    if (!overlay?.classList.contains('active')) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        closeReleaseNotes();
        return;
    }
    if (event.key !== 'Tab') return;
    const focusable = releaseNotesFocusableElements();
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

if (typeof document !== 'undefined') {
    document.addEventListener('keydown', handleReleaseNotesKeydown);
}
