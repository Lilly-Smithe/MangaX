/* Ortak guvenli DOM yardimcilari. Uzak/kullanici metni HTML olarak yorumlanmaz. */
(function initializeSafeDom(global) {
    'use strict';

    const ALLOWED_URL_PROTOCOLS = new Set(['http:', 'https:']);
    const ALLOWED_LOCAL_PROTOCOLS = new Set(['http:', 'https:', 'blob:']);

    function clear(node) {
        if (node) node.replaceChildren();
        return node;
    }

    function text(value) {
        return document.createTextNode(String(value ?? ''));
    }

    function element(tagName, options = {}, children = []) {
        const node = document.createElement(tagName);
        if (options.className) node.className = options.className;
        if (Object.prototype.hasOwnProperty.call(options, 'text')) node.textContent = String(options.text ?? '');
        if (options.title) node.title = String(options.title);
        if (options.type) node.type = String(options.type);
        if (options.disabled !== undefined) node.disabled = Boolean(options.disabled);
        Object.entries(options.attributes || {}).forEach(([name, value]) => {
            if (value !== null && value !== undefined) node.setAttribute(name, String(value));
        });
        Object.entries(options.dataset || {}).forEach(([name, value]) => {
            node.dataset[name] = String(value ?? '');
        });
        const childList = Array.isArray(children) ? children : [children];
        childList.filter(child => child !== null && child !== undefined).forEach(child => {
            node.append(child instanceof Node ? child : text(child));
        });
        return node;
    }

    function icon(classNames, label = '') {
        const node = document.createElement('i');
        node.className = String(classNames || '');
        node.setAttribute('aria-hidden', label ? 'false' : 'true');
        if (label) node.setAttribute('aria-label', String(label));
        return node;
    }

    function safeUrl(value, { fallback = '', allowBlob = false } = {}) {
        const raw = String(value ?? '').trim();
        if (!raw) return fallback;
        try {
            const parsed = new URL(raw, window.location.origin);
            const allowed = allowBlob ? ALLOWED_LOCAL_PROTOCOLS : ALLOWED_URL_PROTOCOLS;
            if (!allowed.has(parsed.protocol)) return fallback;
            return parsed.href;
        } catch (_) {
            return fallback;
        }
    }

    function setImageSource(image, value, fallback = '/static/img/no-cover.jpg') {
        if (!image) return fallback;
        const safeFallback = safeUrl(fallback, { fallback: '/static/img/no-cover.jpg' });
        const resolved = safeUrl(value, { fallback: safeFallback });
        image.src = resolved;
        image.addEventListener('error', () => {
            if (image.src !== safeFallback) image.src = safeFallback;
        }, { once: true });
        return resolved;
    }

    function setBackgroundImage(node, value) {
        if (!node) return false;
        const resolved = safeUrl(value);
        node.style.backgroundImage = resolved ? `url("${resolved.replace(/["\\]/g, '')}")` : '';
        return Boolean(resolved);
    }

    global.MangaXSafeDOM = Object.freeze({ clear, element, icon, safeUrl, setBackgroundImage, setImageSource, text });
})(window);
