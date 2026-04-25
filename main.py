import aiosqlite
import time
import difflib
import httpx
import os
import re
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

load_dotenv()

API_KEY = os.getenv("VIBIX_API_KEY")
BASE_API_URL = "https://vibix.org/"

if not API_KEY:
    print("api token not found")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
DB_PATH = "anime.db"

CACHE = {}
CACHE_TTL = 300


def clean_title(title: str) -> str:
    # Убирает ТВ-1, ТВ-2, 1 сезон, [ТВ-1], (ТВ-2) и прочее
    patterns = [
        r"\[?ТВ-\d+\]?",
        r"\(?ТВ-\d+\)?",
        r"\d+\s*сезон",
        r"Часть\s*\d+",
        r"Season\s*\d+",
        r"-\d+$",
    ]
    for p in patterns:
        title = re.sub(p, "", title, flags=re.IGNORECASE)
    return title.strip().rstrip("-").strip()


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
                SELECT id, title, poster_url, rating_shikimori, year, episodes_count 
                FROM anime 
                WHERE rating_shikimori > 0 
                GROUP BY title 
                ORDER BY year DESC 
                LIMIT 48
                """
            )
            new_animes = await cursor_new.fetchall()

            cursor_pop = await db.execute(
                """
                SELECT id, title, poster_url, rating_shikimori, year, episodes_count
                FROM anime 
                GROUP BY title 
                ORDER BY rating_shikimori DESC 
                LIMIT 48
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


@app.exception_handler(404)
async def custom_404_handler(request: Request, __):
    return templates.TemplateResponse(request=request, name="404.html", status_code=404)


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1)):
    """API живого поиска, полностью повторяющее логику основного поиска."""
    q_lower = q.lower().strip()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Получаем уникальные тайтлы (как в твоем основном поиске)
        cursor = await db.execute(
            "SELECT id, title, title_orig, poster_url, rating_shikimori, year FROM anime GROUP BY title"
        )
        all_animes = await cursor.fetchall()

    exact_matches = []
    titles_map = {}

    for anime in all_animes:
        title = (anime["title"] or "").lower()
        title_orig = (anime["title_orig"] or "").lower()

        # Собираем карту для быстрого поиска по названиям
        if title:
            titles_map[title] = anime
        if title_orig:
            titles_map[title_orig] = anime

        # Логика точного вхождения (exact matches)
        if q_lower in title or q_lower in title_orig:
            exact_matches.append(anime)

    # Если точных совпадений нет, ищем похожие (fuzzy matches)
    if not exact_matches:
        close_titles = difflib.get_close_matches(
            q_lower, titles_map.keys(), n=10, cutoff=0.55
        )

        seen_ids = set()
        results = []
        for ct in close_titles:
            anime = titles_map[ct]
            if anime["id"] not in seen_ids:
                results.append(dict(anime))
                seen_ids.add(anime["id"])
    else:
        # Если есть точные совпадения, берем первые 7 самых рейтинговых
        exact_matches.sort(key=lambda x: x["rating_shikimori"] or 0, reverse=True)
        results = [dict(a) for a in exact_matches[:7]]

    return results


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


async def get_vibix_data(kp_id: str):
    if not kp_id or kp_id == "None":
        print("❌ Ошибка: У этого аниме нет Kinopoisk ID в базе")
        return None

    headers = {"Authorization": f"Bearer {API_KEY}"}
    # Убедись, что адрес API верный (vibix.org или graphicslab.io - проверь в доках)
    url = f"https://vibix.org/api/v1/publisher/videos/kp/{kp_id}"

    async with httpx.AsyncClient() as client:
        try:
            print(f"📡 Запрос к API для kp_id: {kp_id}...")
            response = await client.get(url, headers=headers, timeout=5.0)

            print(f"🔄 Статус ответа API: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"✅ Ссылка получена: {data.get('iframe_url')}")
                return data
            else:
                print(f"⚠️ Ошибка API: {response.text}")
                return None
        except Exception as e:
            print(f"🔥 Критическая ошибка запроса: {e}")
            return None


@app.get("/faq")
async def get_faq(request: Request):
    return templates.TemplateResponse(request=request, name="faq.html", context={})


@app.get("/random")
async def get_random_anime():
    """Выбирает случайное аниме из базы и перенаправляет на его страницу."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # ORDER BY RANDOM() — самый простой способ для SQLite выбрать одну случайную строку
        cursor = await db.execute("SELECT id FROM anime ORDER BY RANDOM() LIMIT 1")
        anime = await cursor.fetchone()

        if anime:
            return RedirectResponse(url=f"/anime/{anime['id']}")

        # Если база пуста, возвращаем на главную
        return RedirectResponse(url="/")


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


@app.get("/robots.txt")
async def robots():
    content = "User-agent: *\nAllow: /\n\nSitemap: https://gochilly.fun/sitemap.xml"
    return Response(content=content, media_type="text/plain")


@app.get("/anime/{anime_id}")
async def get_anime_page(request: Request, anime_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1. Получаем текущее аниме
        curr = await db.execute("SELECT * FROM anime WHERE id = ?", (anime_id,))
        anime = await curr.fetchone()

        if not anime:
            raise HTTPException(status_code=404)

        # --- ПРОДВИНУТАЯ ЛОГИКА РЕКОМЕНДАЦИЙ ---
        genres_list = (
            [g.strip() for g in anime["genres"].split(",")] if anime["genres"] else []
        )
        stop_words = {"аниме", "мультфильм", "короткометражка", "сериал"}

        # Берем до 3-х значимых жанров
        target_genres = [g for g in genres_list if g.lower() not in stop_words][:3]

        # Определяем основную студию (берем первую из списка)
        current_studio = (
            anime["studios"].split(",")[0].strip() if anime["studios"] else ""
        )
        current_type = anime["type"]
        kp_id = anime["kinopoisk_id"]

        similar_animes = []
        if target_genres:
            # Условия для жанров
            where_genres = " OR ".join(["genres LIKE ?" for _ in target_genres])
            # Веса: Жанры (+1 за каждый), Студия (+2), Тип (+1)
            weight_genres = " + ".join(
                [f"(CASE WHEN genres LIKE ? THEN 1 ELSE 0 END)" for _ in target_genres]
            )

            # Параметры запроса
            params = [f"%{g}%" for g in target_genres]  # Для WHERE
            params += [f"%{g}%" for g in target_genres]  # Для веса жанров
            params.append(
                f"%{current_studio}%" if current_studio else ""
            )  # Для веса студии
            params.append(current_type)  # Для веса типа
            params.append(anime_id)  # Исключаем само аниме
            params.append(kp_id)  # Исключаем текущую франшизу

            query = f"""
                SELECT id, title, poster_url, rating_shikimori, year,
                (({weight_genres}) + 
                 (CASE WHEN studios LIKE ? THEN 2 ELSE 0 END) + 
                 (CASE WHEN type = ? THEN 1 ELSE 0 END)) as weight
                FROM anime 
                WHERE ({where_genres}) 
                AND id != ? 
                AND (kinopoisk_id IS NULL OR kinopoisk_id != ?)
                -- Группируем по KP_ID, а если его нет — по ID самого аниме
                GROUP BY COALESCE(kinopoisk_id, id) 
                ORDER BY weight DESC, rating_shikimori DESC
                LIMIT 24
            """

            cursor_sim = await db.execute(query, params)
            similar_animes = await cursor_sim.fetchall()

        # 2. Поиск сезонов (как и раньше)
        cursor_seasons = await db.execute(
            "SELECT id, title, player_link FROM anime WHERE kinopoisk_id = ? ORDER BY year ASC",
            (kp_id,),
        )
        seasons = await cursor_seasons.fetchall()
        display_title = clean_title(anime["title"])

        return templates.TemplateResponse(
            request=request,
            name="anime.html",
            context={
                "anime": anime,
                "clean_title": display_title,
                "seasons": seasons,
                "similar_animes": similar_animes,
            },
        )
