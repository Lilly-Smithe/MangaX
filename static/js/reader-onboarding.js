const READER_ONBOARDING_COMPLETED_KEY = 'mangax-reader-onboarding-completed-v1';
const READER_ONBOARDING_STEP_COUNT = 4;

let readerOnboardingStep = 1;
let readerOnboardingPreviousFocus = null;

function isReaderOnboardingCompleted() {
    try {
        return localStorage.getItem(READER_ONBOARDING_COMPLETED_KEY) === 'true';
    } catch (_) {
        return false;
    }
}

function completeReaderOnboarding() {
    try {
        localStorage.setItem(READER_ONBOARDING_COMPLETED_KEY, 'true');
    } catch (_) { /* uygulama bu oturumda devam eder */ }
    if (typeof persistOnboardingCompleted === 'function') persistOnboardingCompleted();
}

function readerOnboardingFocusableElements() {
    const activeStep = document.querySelector('#onboarding-overlay .onboarding-step.active');
    if (!activeStep) return [];
    return [...activeStep.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')]
        .filter(element => !element.classList.contains('hidden'));
}

function setReaderOnboardingStep(step) {
    readerOnboardingStep = Math.max(1, Math.min(READER_ONBOARDING_STEP_COUNT, Number(step) || 1));
    document.querySelectorAll('#onboarding-overlay .onboarding-step').forEach(panel => {
        panel.classList.toggle('active', Number(panel.dataset.step) === readerOnboardingStep);
    });
    document.querySelectorAll('#onboarding-overlay .onboarding-progress-dot').forEach(dot => {
        const dotStep = Number(dot.dataset.step);
        dot.classList.toggle('active', dotStep === readerOnboardingStep);
        dot.classList.toggle('complete', dotStep < readerOnboardingStep);
    });
    const counter = document.getElementById('onboarding-step-counter');
    if (counter) counter.textContent = `${readerOnboardingStep} / ${READER_ONBOARDING_STEP_COUNT}`;
    const heading = document.querySelector(`#onboarding-overlay .onboarding-step[data-step="${readerOnboardingStep}"] h2`);
    requestAnimationFrame(() => heading?.focus());
}

function openOnboarding() {
    const overlay = document.getElementById('onboarding-overlay');
    if (!overlay) return;
    readerOnboardingPreviousFocus = document.activeElement;
    overlay.classList.add('active');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('onboarding-open');
    setReaderOnboardingStep(1);
}

function closeReaderOnboarding() {
    const overlay = document.getElementById('onboarding-overlay');
    if (!overlay) return;
    overlay.classList.remove('active');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('onboarding-open');
    if (readerOnboardingPreviousFocus && typeof readerOnboardingPreviousFocus.focus === 'function') {
        readerOnboardingPreviousFocus.focus();
    }
    readerOnboardingPreviousFocus = null;
}

function skipReaderOnboarding() {
    completeReaderOnboarding();
    closeReaderOnboarding();
    if (typeof switchTab === 'function') switchTab('library');
    if (typeof showToast === 'function') showToast('MangaX Reader hazır.', 'info');
}

function finishReaderOnboarding({ openImporter = false } = {}) {
    completeReaderOnboarding();
    closeReaderOnboarding();
    if (typeof switchTab === 'function') switchTab('library');
    if (openImporter && typeof openLocalImportDialog === 'function') {
        openLocalImportDialog();
    } else if (typeof showToast === 'function') {
        showToast('Yerel kütüphanen hazır.', 'success');
    }
}

function handleReaderOnboardingKeydown(event) {
    const overlay = document.getElementById('onboarding-overlay');
    if (!overlay?.classList.contains('active')) return;
    if (event.key === 'Escape') {
        event.preventDefault();
        skipReaderOnboarding();
        return;
    }
    if (event.key !== 'Tab') return;
    const focusable = readerOnboardingFocusableElements();
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

async function initializeReaderOnboarding() {
    document.addEventListener('keydown', handleReaderOnboardingKeydown);
    if (typeof applyStartupExperience === 'function') {
        await applyStartupExperience(openOnboarding);
    } else if (!isReaderOnboardingCompleted()) {
        openOnboarding();
    }
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', initializeReaderOnboarding);
}
