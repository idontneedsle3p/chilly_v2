from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.templating import Jinja2Templates
import aiosqlite
import time  # Добавили модуль для работы со временем
import difflib  # <--- Добавить эту строку в самом начале

app = FastAPI()
templates = Jinja2Templates(directory="templates")
DB_PATH = "anime.db"

# --- НАСТРОЙКИ КЭША ---
CACHE = {}
CACHE_TTL = 300  # Время жизни кэша в секундах (300 сек = 5 минут)


@app.get("/")
async def read_root(request: Request):
    """Главная страница с кэшированием."""
    current_time = time.time()

    if "home_page" in CACHE and (current_time - CACHE["home_page"]["time"]) < CACHE_TTL:
        print("🚀 Отдаем главную страницу из КЭША!")
        new_animes = CACHE["home_page"]["new"]
        popular_animes = CACHE["home_page"]["popular"]

    else:
        print("🐢 Идем в базу данных...")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # ИСПРАВЛЕНИЕ 1: Добавили WHERE rating > 0 и GROUP BY title
            cursor_new = await db.execute(
                """
                SELECT id, title, poster_url, rating_shikimori, year 
                FROM anime 
                WHERE rating_shikimori > 0 
                GROUP BY title 
                ORDER BY year DESC 
                LIMIT 12
                """
            )
            new_animes = await cursor_new.fetchall()

            # ИСПРАВЛЕНИЕ 2: Добавили GROUP BY title от дубликатов
            cursor_pop = await db.execute(
                """
                SELECT id, title, poster_url, rating_shikimori, year 
                FROM anime 
                GROUP BY title 
                ORDER BY rating_shikimori DESC 
                LIMIT 12
                """
            )
            popular_animes = await cursor_pop.fetchall()

        CACHE["home_page"] = {
            "new": new_animes,
            "popular": popular_animes,
            "time": current_time,
        }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "new_animes": new_animes,
            "popular_animes": popular_animes,
        },
    )


@app.get("/search")
async def search_anime(request: Request, q: str = Query(..., min_length=1)):
    """Умная страница результатов поиска (с защитой от опечаток и регистра)."""

    # Переводим запрос пользователя в нижний регистр и убираем пробелы по краям
    q_lower = q.lower().strip()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Забираем ВСЕ аниме из базы (SQLite отдаст это за пару миллисекунд)
        cursor = await db.execute(
            "SELECT id, title, title_orig, poster_url, rating_shikimori, year FROM anime GROUP BY title"
        )
        all_animes = await cursor.fetchall()

    exact_matches = []
    fuzzy_matches = []

    # Словарь для поиска по опечаткам
    titles_map = {}

    # 2. ИЩЕМ ТОЧНЫЕ СОВПАДЕНИЯ (игнорируя большие/маленькие буквы)
    for anime in all_animes:
        # Переводим названия из базы в нижний регистр для сравнения
        title = (anime["title"] or "").lower()
        title_orig = (anime["title_orig"] or "").lower()

        # Сохраняем в словарь для следующего шага
        if title:
            titles_map[title] = anime
        if title_orig:
            titles_map[title_orig] = anime

        # Если слово есть в названии - это точное совпадение
        if q_lower in title or q_lower in title_orig:
            exact_matches.append(anime)

    # 3. ИЩЕМ ОПЕЧАТКИ (если точных совпадений нет)
    if not exact_matches:
        # get_close_matches ищет слова, похожие на q_lower.
        # cutoff=0.55 означает, что слова должны совпадать хотя бы на 55%
        close_titles = difflib.get_close_matches(
            q_lower, titles_map.keys(), n=20, cutoff=0.55
        )

        seen_ids = set()
        for ct in close_titles:
            anime = titles_map[ct]
            # Избегаем дубликатов
            if anime["id"] not in seen_ids:
                fuzzy_matches.append(anime)
                seen_ids.add(anime["id"])

    # Если нашли точное совпадение - показываем его. Если нет - показываем похожие.
    results = exact_matches if exact_matches else fuzzy_matches

    # Ограничиваем вывод до 30 карточек, чтобы не перегружать страницу
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={"request": request, "results": results[:30], "query": q},
    )


@app.get("/anime/{anime_id}")
async def read_anime(request: Request, anime_id: str):
    """Страница конкретного аниме (без изменений)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM anime WHERE id = ?", (anime_id,))
        anime = await cursor.fetchone()

    if anime is None:
        raise HTTPException(status_code=404, detail="Аниме не найдено")

    return templates.TemplateResponse(
        request=request, name="anime.html", context={"request": request, "anime": anime}
    )
