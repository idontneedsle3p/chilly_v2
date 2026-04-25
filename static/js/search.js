document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('live-search');
    const resultsContainer = document.getElementById('search-results');
    const resultsList = document.getElementById('results-list');
    const overlay = document.getElementById('search-overlay');

    // КРИТИЧНО: Если элемента поиска нет на странице, просто не запускаем логику
    if (!searchInput || !overlay || !resultsContainer) {
        console.log("🔍 Живой поиск на этой странице не найден, пропускаем инициализацию.");
        return;
    }

    let debounceTimer;

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        clearTimeout(debounceTimer);

        if (query.length < 2) {
            hideSearch();
            return;
        }

        showSearch();

        debounceTimer = setTimeout(() => {
            fetch(`/api/search?q=${encodeURIComponent(query)}`)
                .then(res => res.json())
                .then(data => {
                    renderLiveResults(data);
                })
                .catch(err => console.error("Ошибка поиска:", err));
        }, 300);
    });

    function renderLiveResults(data) {
        if (data.length === 0) {
            resultsList.innerHTML = '<div class="p-4 text-center text-sm text-slate-500">Ничего не найдено</div>';
            return;
        }

        resultsList.innerHTML = data.map(anime => `
            <a href="/anime/${anime.id}" class="flex items-center gap-3 p-2 hover:bg-white/5 rounded-xl transition group">
                <img src="${anime.poster_url}" class="w-10 h-14 object-cover rounded-lg shadow-md" onerror="this.src='/static/no-poster.jpg'">
                <div class="flex-grow min-w-0">
                    <div class="text-sm font-bold text-white truncate group-hover:text-indigo-400">${anime.title}</div>
                    <div class="text-[10px] text-slate-500 uppercase mt-0.5">${anime.year} • ★ ${anime.rating_shikimori}</div>
                </div>
            </a>
        `).join('') + `
            <button onclick="this.closest('form').submit();" class="w-full py-2 mt-2 border-t border-white/5 text-xs font-bold text-indigo-400 hover:text-white transition">
                Показать все результаты
            </button>
        `;
    }

    function showSearch() {
        overlay.classList.remove('hidden');
        resultsContainer.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }

    function hideSearch() {
        overlay.classList.add('hidden');
        resultsContainer.classList.add('hidden');
        document.body.style.overflow = 'auto';
    }

    overlay.addEventListener('click', hideSearch);
});