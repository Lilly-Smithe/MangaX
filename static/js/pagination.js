// Ortak, erişilebilir istemci tarafı sayfalama yardımcısı.
(function initializeMangaXPagination(global) {
    function clampPage(page, totalPages) {
        const parsed = Number.parseInt(page, 10) || 1;
        return Math.min(Math.max(parsed, 1), Math.max(totalPages, 1));
    }

    function paginate(items, page, pageSize) {
        const safeItems = Array.isArray(items) ? items : [];
        const safePageSize = Math.max(Number.parseInt(pageSize, 10) || 1, 1);
        const totalPages = Math.max(Math.ceil(safeItems.length / safePageSize), 1);
        const currentPage = clampPage(page, totalPages);
        const start = (currentPage - 1) * safePageSize;
        return {
            items: safeItems.slice(start, start + safePageSize),
            currentPage,
            totalPages,
        };
    }

    function createButton(label, { disabled = false, current = false, onClick } = {}) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'mangax-pagination-btn';
        button.textContent = label;
        button.disabled = disabled;
        if (current) button.setAttribute('aria-current', 'page');
        if (typeof onClick === 'function') button.addEventListener('click', onClick);
        return button;
    }

    function pageWindow(currentPage, totalPages) {
        const pages = new Set([1, totalPages]);
        for (let page = currentPage - 2; page <= currentPage + 2; page += 1) {
            if (page > 0 && page <= totalPages) pages.add(page);
        }
        return [...pages].sort((a, b) => a - b);
    }

    function render({ host, currentPage, totalItems, pageSize, onPageChange, label = 'Sayfalar' }) {
        if (!host) return null;
        const safeTotal = Math.max(Number.parseInt(totalItems, 10) || 0, 0);
        const safePageSize = Math.max(Number.parseInt(pageSize, 10) || 1, 1);
        const totalPages = Math.max(Math.ceil(safeTotal / safePageSize), 1);
        const state = { currentPage: clampPage(currentPage, totalPages), totalPages };
        host.replaceChildren();
        if (state.totalPages <= 1) {
            host.classList.add('hidden');
            return state;
        }

        host.classList.remove('hidden');
        const nav = document.createElement('nav');
        nav.className = 'mangax-pagination-nav';
        nav.setAttribute('aria-label', label);
        nav.appendChild(createButton('‹ Önceki', {
            disabled: state.currentPage === 1,
            onClick: () => onPageChange(state.currentPage - 1),
        }));

        const pages = pageWindow(state.currentPage, state.totalPages);
        pages.forEach((page, index) => {
            if (index && page - pages[index - 1] > 1) {
                const ellipsis = document.createElement('span');
                ellipsis.className = 'mangax-pagination-ellipsis';
                ellipsis.textContent = '…';
                ellipsis.setAttribute('aria-hidden', 'true');
                nav.appendChild(ellipsis);
            }
            nav.appendChild(createButton(String(page), {
                current: page === state.currentPage,
                onClick: () => onPageChange(page),
            }));
        });

        nav.appendChild(createButton('Sonraki ›', {
            disabled: state.currentPage === state.totalPages,
            onClick: () => onPageChange(state.currentPage + 1),
        }));
        host.appendChild(nav);
        return state;
    }

    global.MangaXPagination = Object.freeze({ paginate, render });
})(window);
