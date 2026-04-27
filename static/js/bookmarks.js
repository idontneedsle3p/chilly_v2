const LIST_KEYS = ['planned', 'watching', 'watched'];

// Получить данные из LocalStorage
function getStorage() {
    let data = localStorage.getItem('chilly_bookmarks');
    return data ? JSON.parse(data) : { planned: [], watching: [], watched: [] };
}

// Сохранить в LocalStorage
function saveStorage(data) {
    localStorage.setItem('chilly_bookmarks', JSON.stringify(data));
}

// Добавить или сменить статус
function setAnimeStatus(id, title, poster, status) {
    console.log(`Изменение статуса: ${id} -> ${status}`);
    let data = getStorage();

    // Удаляем из всех списков
    LIST_KEYS.forEach(key => {
        data[key] = data[key].filter(item => item.id !== id);
    });

    if (status !== 'none') {
        data[status].push({ id, title, poster });
    }

    saveStorage(data);

    // 1. Обновляем кнопки на странице аниме (если мы на ней)
    if (typeof updateButtonsUI === 'function') {
        updateButtonsUI(status);
    }

    // 2. СРАЗУ перерисовываем содержимое модалки
    renderBookmarks();
}

// Рендеринг списка в модалке
function renderBookmarks() {
    const data = getStorage();
    LIST_KEYS.forEach(key => {
        const container = document.getElementById(`list-${key}`);
        if (!container) return;

        if (data[key].length === 0) {
            container.innerHTML = `<p class="text-slate-500 text-sm py-4 text-center">Список пуст</p>`;
            return;
        }

        container.innerHTML = data[key].map(item => {
            // В закладках мы теперь тоже используем slug для ссылок
            return `
            <div class="flex items-center gap-4 p-2 hover:bg-white/5 rounded-xl transition group">
                <img src="${item.poster}" class="w-12 h-16 object-cover rounded-lg shadow-md">
                <div class="flex-grow min-w-0">
                    <a href="/anime/${item.id}" class="text-sm font-bold text-white truncate block hover:text-indigo-400">${item.title}</a>
                </div>
                <button onclick="setAnimeStatus('${item.id}', '', '', 'none')" class="opacity-0 group-hover:opacity-100 p-2 text-slate-500 hover:text-red-400 transition">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                </button>
            </div>
        `}).join('');
    });
}

function openBookmarks() {
    document.getElementById('bookmarks-modal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    renderBookmarks();
}

function closeBookmarks() {
    document.getElementById('bookmarks-modal').classList.add('hidden');
    document.body.style.overflow = 'auto';
}

// Добавь это в конец bookmarks.js

// Сохранить последний просмотр
function saveLastViewed(id, title, poster) {
    const lastViewed = {
        id: id,
        title: title,
        poster: poster,
        time: Date.now()
    };
    localStorage.setItem('chilly_last_viewed', JSON.stringify(lastViewed));
}

// Получить последний просмотр
function getLastViewed() {
    const data = localStorage.getItem('chilly_last_viewed');
    return data ? JSON.parse(data) : null;
}