from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.templating import Jinja2Templates
import aiosqlite
import time
import difflib
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, Request, HTTPException, Query, Response

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
DB_PATH = "anime.db"

CACHE = {}
CACHE_TTL = 300


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

    q_lower = q.lower().strip()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT id, title, title_orig, poster_url, rating_shikimori, year FROM anime GROUP BY title"
        )
        all_animes = await cursor.fetchall()

    exact_matches = []
    fuzzy_matches = []

    titles_map = {}

    for anime in all_animes:
        title = (anime["title"] or "").lower()
        title_orig = (anime["title_orig"] or "").lower()

        if title:
            titles_map[title] = anime
        if title_orig:
            titles_map[title_orig] = anime

        if q_lower in title or q_lower in title_orig:
            exact_matches.append(anime)

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

    results = exact_matches if exact_matches else fuzzy_matches

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={"request": request, "results": results[:30], "query": q},
    )


@app.get("/catalog")
async def get_catalog(request: Request, genre: str = Query(None)):
    genres_list = [
        "Экшен",
        "Фэнтези",
        "Приключения",
        "Комедия",
        "Драма",
        "Романтика",
        "Сёнен",
        "Детектив",
        "Психология",
        "Триллер",
    ]

    animes = []
    if genre:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Ищем жанр внутри строки. % - это любой текст до и после
            cursor = await db.execute(
                "SELECT * FROM anime WHERE genres LIKE ? LIMIT 40", (f"%{genre}%",)
            )
            animes = await cursor.fetchall()

    return templates.TemplateResponse(
        request=request,
        name="catalog.html",
        context={"genres_list": genres_list, "selected_genre": genre, "animes": animes},
    )


@app.get("/faq")
async def get_faq(request: Request):
    return templates.TemplateResponse(request=request, name="faq.html", context={})


@app.get("/roadmap")
async def get_roadmap(request: Request):
    tasks = [
        {
            "title": "Каталог и жанры",
            "desc": "Запуск полноценного поиска по категориям.",
            "status": "done",
        },
        {
            "title": "OpenGraph разметка",
            "desc": "Красивые превью ссылок в соцсетях и Telegram.",
            "status": "done",
        },
        {
            "title": "FAQ и футер",
            "desc": "Добавление справочной информации и навигации.",
            "status": "done",
        },
        {
            "title": "Кнопка «Рандомное аниме»",
            "desc": "Быстрый переход к случайному тайтлу из базы.",
            "status": "progress",
        },
        {
            "title": "Фильтры по годам и типу",
            "desc": "Возможность отсеять только фильмы или аниме-сериалы.",
            "status": "progress",
        },
        {
            "title": "Личный кабинет (локальный)",
            "desc": "Список «Избранного» и история просмотров без регистрации.",
            "status": "planned",
        },
        {
            "title": "Комментарии",
            "desc": "Обсуждение серий прямо под плеером.",
            "status": "planned",
        },
        {
            "title": "PWA Приложение",
            "desc": "Возможность установить сайт на телефон как иконку.",
            "status": "planned",
        },
    ]

    return templates.TemplateResponse(
        request=request, name="roadmap.html", context={"tasks": tasks}
    )


SITEMAP_CACHE = {"xml": "", "time": 0}
SITEMAP_TTL = 86400


@app.get("/sitemap.xml")
async def get_sitemap(request: Request):
    """Динамическая карта сайта для Яндекса и Google."""
    global SITEMAP_CACHE
    current_time = time.time()

    if SITEMAP_CACHE["xml"] and (current_time - SITEMAP_CACHE["time"]) < SITEMAP_TTL:
        return Response(content=SITEMAP_CACHE["xml"], media_type="application/xml")

    base_url = str(request.base_url).rstrip("/")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, updated_at FROM anime")
        animes = await cursor.fetchall()

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{base_url}/</loc>
        <changefreq>hourly</changefreq>
        <priority>1.0</priority>
    </url>
"""

    for anime in animes:
        lastmod_tag = (
            f"\n        <lastmod>{anime['updated_at']}</lastmod>"
            if anime["updated_at"]
            else ""
        )

        xml_content += f"""
    <url>
        <loc>{base_url}/anime/{anime["id"]}</loc>{lastmod_tag}
        <changefreq>weekly</changefreq>
        <priority>0.8</priority>
    </url>"""

    xml_content += "\n</urlset>"

    SITEMAP_CACHE["xml"] = xml_content
    SITEMAP_CACHE["time"] = current_time

    return Response(content=xml_content, media_type="application/xml")


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
