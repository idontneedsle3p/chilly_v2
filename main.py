import asyncpg
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
from datetime import datetime, timezone
from datetime import date
from contextlib import asynccontextmanager

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://admin:123@localhost:5432/anime_db"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем пул соединений при запуске
    app.state.pool = await asyncpg.create_pool(DATABASE_URL)
    yield
    # Закрываем пул при выключении
    await app.state.pool.close()


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,  # Отключает /docs
    redoc_url=None,  # Отключает /redoc
    openapi_url=None,  # Отключает /openapi.json
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CACHE = {}
CACHE_TTL = 300


def timeago(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        if diff.days > 0:
            return dt.strftime("%d.%m")
        if diff.seconds < 3600:
            return f"{diff.seconds // 60} мин. назад"
        return f"{diff.seconds // 3600} ч. назад"
    except:
        return value


templates.env.filters["timeago"] = timeago


def clean_genres(value):
    if not value:
        return ""
    genres = [g.strip().lower() for g in value.split(",")]
    unique_genres = list(dict.fromkeys(genres))
    return ", ".join(unique_genres)


def translate_type(value):
    if not value:
        return ""
    mapping = {
        "anime-serial": "Аниме",
        "anime": "Фильм",
        "ova": "ova",
        "ona": "ona",
        "special": "спешл",
    }
    return mapping.get(value.lower(), value.lower())


templates.env.filters["clean_genres"] = clean_genres
templates.env.filters["translate_type"] = translate_type


def clean_title(title: str) -> str:
    patterns = [
        r"//.*",  # Уберет всё после // (для .hack//)
        r":.*",  # Уберет всё после двоеточия (для Гинтамы)
        r"\[?ТВ-\d+\]?",
        r"\d+\s*сезон",
        r"Season\s*\d+",
        r"Часть\s*\d+",
    ]
    for p in patterns:
        title = re.sub(p, "", title, flags=re.IGNORECASE)
    return title.strip()


# НЕ ЗАБУДЬ ЗАРЕГИСТРИРОВАТЬ ФИЛЬТР:
templates.env.filters["clean_title"] = clean_title


@app.get("/")
async def read_root(request: Request):
    current_time = time.time()
    if "home_page" in CACHE and (current_time - CACHE["home_page"]["time"]) < CACHE_TTL:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"request": request, **CACHE["home_page"]["data"]},
        )

    async with request.app.state.pool.acquire() as db:
        # В Postgres для удаления дублей по названию используем DISTINCT ON
        new_animes = await db.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (title) id, slug, title, poster_url, rating_shikimori, year, episodes_count, updated_at 
                FROM anime 
                WHERE year >= 2025 AND rating_shikimori > $1
                ORDER BY title, updated_at DESC
            ) AS sub
            ORDER BY updated_at DESC 
            LIMIT 48
        """,
            0.0,
        )

        popular_animes = await db.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (
                    LOWER(TRIM(regexp_replace(title, '(\/\/|:|\[|\(|Season|Сезон).*', '', 'gi')))
                ) id, slug, title, poster_url, rating_shikimori, year, episodes_count, rating_shikimori as score
                FROM anime
                WHERE type != 'anime'
                ORDER BY 
                    LOWER(TRIM(regexp_replace(title, '(\/\/|:|\[|\(|Season|Сезон).*', '', 'gi'))),
                    rating_shikimori DESC
            ) AS sub
            ORDER BY score DESC 
            LIMIT 120
        """)

        isekai_animes = await db.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(kinopoisk_id, id::text)) 
                    id, slug, title, poster_url, rating_shikimori, year, episodes_count, rating_shikimori as score, kinopoisk_id
                FROM anime 
                WHERE genres ILIKE '%Исэкай%' AND type != 'anime'
                ORDER BY COALESCE(kinopoisk_id, id::text), rating_shikimori DESC
            ) AS sub
            ORDER BY score DESC 
            LIMIT 120
        """)

        romantic_animes = await db.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(kinopoisk_id, id::text)) 
                    id, slug, title, poster_url, rating_shikimori, year, episodes_count, rating_shikimori as score, kinopoisk_id
                FROM anime 
                WHERE genres ILIKE '%Романтика%' AND type != 'anime'
                ORDER BY COALESCE(kinopoisk_id, id::text), rating_shikimori DESC
            ) AS sub
            ORDER BY score DESC 
            LIMIT 120
        """)

        phys_animes = await db.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(kinopoisk_id, id::text)) 
                    id, slug, title, poster_url, rating_shikimori, year, episodes_count, rating_shikimori as score, kinopoisk_id
                FROM anime 
                WHERE genres ILIKE '%Психологическое%' AND type != 'anime'
                ORDER BY COALESCE(kinopoisk_id, id::text), rating_shikimori DESC
            ) AS sub
            ORDER BY score DESC 
            LIMIT 120
        """)

        school_animes = await db.fetch("""
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(kinopoisk_id, id::text)) 
                    id, slug, title, poster_url, rating_shikimori, year, episodes_count, rating_shikimori as score, kinopoisk_id
                FROM anime 
                WHERE genres ILIKE '%Школа%' AND type != 'anime'
                ORDER BY COALESCE(kinopoisk_id, id::text), rating_shikimori DESC
            ) AS sub
            ORDER BY score DESC 
            LIMIT 120
        """)

    data = {
        "new_animes": new_animes,
        "popular_animes": popular_animes,
        "isekai_animes": isekai_animes,
        "romantic_animes": romantic_animes,
        "phys_animes": phys_animes,
        "school_animes": school_animes,
    }
    CACHE["home_page"] = {"data": data, "time": current_time}
    return templates.TemplateResponse(
        request=request, name="index.html", context={"request": request, **data}
    )


@app.exception_handler(404)
async def custom_404_handler(request: Request, __):
    return templates.TemplateResponse(request=request, name="404.html", status_code=404)


@app.get("/api/search")
async def api_search(request: Request, q: str = Query(..., min_length=1)):
    q_clean = q.strip()
    q_like = f"%{q_clean.lower()}%"

    async with request.app.state.pool.acquire() as db:
        # Используем DISTINCT ON, чтобы в подсказках не было дублей сезонов
        results = await db.fetch(
            """
            SELECT DISTINCT ON (title) id, slug, title, title_orig, poster_url, rating_shikimori, year,
                   similarity(title, $1) as sml
            FROM anime 
            WHERE title % $1 
               OR title ILIKE $2 
               OR title_orig ILIKE $2
            ORDER BY title, sml DESC, rating_shikimori DESC 
            LIMIT 7
        """,
            q_clean,
            q_like,
        )

    # Сортируем по схожести, чтобы самые точные совпадения были сверху
    sorted_results = sorted(results, key=lambda x: x["sml"], reverse=True)
    return [dict(r) for r in sorted_results]


@app.get("/search")
async def search_anime(request: Request, q: str = Query(..., min_length=1)):
    q_clean = q.strip()
    q_like = f"%{q_clean.lower()}%"  # Подготовка для частичного совпадения

    async with request.app.state.pool.acquire() as db:
        results = await db.fetch(
            """
            SELECT DISTINCT ON (title) id, slug, title, title_orig, poster_url, rating_shikimori, year,
                   similarity(title, $1) as sml
            FROM (
                SELECT * FROM anime 
                -- Ищем ИЛИ по схожести (для опечаток), ИЛИ по точному вхождению (как в SQLite)
                WHERE title % $1 
                   OR title ILIKE $2 
                   OR title_orig ILIKE $2
            ) as sub
            ORDER BY title, sml DESC
            LIMIT 30
        """,
            q_clean,
            q_like,
        )

    sorted_results = sorted(results, key=lambda x: x["sml"], reverse=True)

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={"request": request, "results": sorted_results, "query": q},
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
        "Триллер",
    ]
    animes = []
    if genre:
        async with request.app.state.pool.acquire() as db:
            animes = await db.fetch(
                """
                SELECT id, slug, title, poster_url, rating_shikimori, year, episodes_count 
                FROM anime WHERE genres ILIKE $1 LIMIT 40
            """,
                f"%{genre}%",
            )
    return templates.TemplateResponse(
        request=request,
        name="catalog.html",
        context={"genres_list": genres_list, "selected_genre": genre, "animes": animes},
    )


@app.get("/faq")
async def get_faq(request: Request):
    return templates.TemplateResponse(request=request, name="faq.html", context={})


@app.get("/all-anime", response_class=HTMLResponse)
async def all_anime_archive(request: Request, page: int = Query(1, ge=1)):
    # Количество аниме на одну страницу архива
    per_page = 150
    offset = (page - 1) * per_page

    async with request.app.state.pool.acquire() as db:
        # 1. Считаем общее количество тайтлов в базе данных для пагинации
        total_count = await db.fetchval("SELECT COUNT(*) FROM anime")

        # 2. Достаем порцию аниме (id, slug и title для красивого текста ссылки)
        rows = await db.fetch(
            "SELECT id, slug, title FROM anime ORDER BY id DESC LIMIT $1 OFFSET $2",
            per_page,
            offset,
        )

    # Высчитываем сколько всего страниц получится
    total_pages = (total_count + per_page - 1) // per_page

    # Генерируем массив HTML-ссылок для карточек аниме
    anime_links = []
    for r in rows:
        slug_or_id = r["slug"] if r["slug"] else r["id"]
        title = r["title"] if r["title"] else f"Аниме ID {r['id']}"
        anime_links.append(
            f'<li><a href="/anime/{slug_or_id}" class="text-slate-300 hover:text-indigo-400 transition-colors">{title}</a></li>'
        )

    # Генерируем циферки переключения страниц (пагинацию)
    pagination_links = []
    for p in range(1, total_pages + 1):
        if p == page:
            # Активная страница подсвечена
            pagination_links.append(
                f'<span class="px-3 py-1 bg-indigo-600 text-white font-bold rounded-lg">{p}</span>'
            )
        else:
            pagination_links.append(
                f'<a href="/all-anime?page={p}" class="px-3 py-1 bg-slate-900 border border-white/5 text-slate-400 hover:text-white rounded-lg transition-colors">{p}</a>'
            )

    # Собираем легкую HTML-страницу на Tailwind
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Архив всех аниме | Карта сайта ChillyAnime</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-300 font-sans antialiased p-6 md:p-12">
        <div class="container mx-auto max-w-5xl">
            <header class="mb-8 border-b border-white/5 pb-4">
                <a href="/" class="text-sm text-slate-500 hover:text-indigo-400 transition mb-2 inline-block">← На главную</a>
                <h1 class="text-3xl font-extrabold text-white tracking-tight">Навигационный архив аниме (Страница {page} из {total_pages})</h1>
                <p class="text-sm text-slate-500 mt-1">Здесь собран полный список релизов нашего сайта для поисковых систем.</p>
            </header>

            <main>
                <ul class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 list-inside list-square mb-12">
                    {"".join(anime_links)}
                </ul>
            </main>

            <footer class="border-t border-white/5 pt-6">
                <div class="flex flex-wrap gap-2 items-center justify-center">
                    {"".join(pagination_links)}
                </div>
            </footer>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/random")
async def get_random_anime(request: Request):
    async with request.app.state.pool.acquire() as db:
        # RANDOM() в Postgres работает так же
        anime = await db.fetchrow(
            "SELECT id, slug FROM anime ORDER BY RANDOM() LIMIT 1"
        )
        if anime:
            target = anime["slug"] or anime["id"]
            return RedirectResponse(url=f"/anime/{target}")
    return RedirectResponse(url="/")


@app.get("/anime/{identifier}")
async def get_anime_page(request: Request, identifier: str):
    async with request.app.state.pool.acquire() as db:
        anime = await db.fetchrow(
            "SELECT * FROM anime WHERE slug = $1 OR id = $1", identifier
        )
        if not anime:
            raise HTTPException(status_code=404)
        if identifier == anime["id"] and anime["slug"]:
            return RedirectResponse(url=f"/anime/{anime['slug']}", status_code=301)

        genres = (
            [g.strip() for g in anime["genres"].split(",")] if anime["genres"] else []
        )
        current_studio = (
            anime["studios"].split(",")[0].strip() if anime["studios"] else ""
        )

        similar_animes = []
        if genres:
            g1 = f"%{genres[0]}%"
            g2 = f"%{genres[1]}%" if len(genres) > 1 else g1
            g3 = f"%{genres[2]}%" if len(genres) > 2 else g1

            similar_animes = await db.fetch(
                """
                SELECT * FROM (
                    -- 1. Сначала отбираем по одному лучшему аниме из каждой франшизы
                    SELECT * FROM (
                        SELECT DISTINCT ON (COALESCE(kinopoisk_id, id::text)) 
                            id, slug, title, poster_url, rating_shikimori, year, kinopoisk_id
                        FROM anime 
                        WHERE (genres ILIKE $1 OR genres ILIKE $2 OR genres ILIKE $3) 
                          AND id != $4
                          AND rating_shikimori >= 7.0 
                        -- Важно: сначала сортируем по ID франшизы, потом по рейтингу
                        ORDER BY COALESCE(kinopoisk_id, id::text), rating_shikimori DESC
                    ) as unique_franchises
                    -- 2. Берем ТОП-30 самых популярных франшиз
                    ORDER BY rating_shikimori DESC
                    LIMIT 30
                ) as random_pool
                -- 3. Перемешиваем их для разнообразия
                ORDER BY RANDOM() 
                LIMIT 30
            """,
                g1,
                g2,
                g3,
                anime["id"],
            )

        seasons = await db.fetch(
            """
            SELECT id, slug, title, player_link 
            FROM anime 
            WHERE kinopoisk_id = $1 
            ORDER BY 
                year ASC, 
                -- 1. Пытаемся извлечь номер из "ТВ-X" и сортируем как число
                (substring(title from 'ТВ-(\d+)')::int) NULLS LAST,
                -- 2. Если номера ТВ одинаковые, проверяем слова "первый/второй"
                CASE 
                    WHEN title ~* 'первый сезон|часть 1' THEN 1
                    WHEN title ~* 'второй сезон|часть 2' THEN 2
                    WHEN title ~* 'третий сезон|часть 3' THEN 3
                    ELSE 5
                END ASC,
                -- 3. Напоследок обычная сортировка
                title ASC
        """,
            anime["kinopoisk_id"],
        )

        return templates.TemplateResponse(
            request=request,
            name="anime.html",
            context={
                "anime": anime,
                "clean_title": clean_title(anime["title"]),
                "seasons": seasons,
                "similar_animes": similar_animes,
            },
        )


@app.get("/support", response_class=HTMLResponse)
async def get_support_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="support.html", context={"request": request}
    )


SITEMAP_CACHE = {"xml": "", "time": 0}
SITEMAP_TTL = 86400


@app.get("/sitemap-new.xml")
async def sitemap_index():
    """Главный индексный файл"""
    current_date = datetime.now(timezone.utc).date().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "    <sitemap>\n"
        "        <loc>https://gochilly.fun/sitemap-main.xml</loc>\n"
        f"        <lastmod>{current_date}</lastmod>\n"
        "    </sitemap>\n"
        "    <sitemap>\n"
        "        <loc>https://gochilly.fun/sitemap-anime.xml</loc>\n"
        f"        <lastmod>{current_date}</lastmod>\n"
        "    </sitemap>\n"
        "</sitemapindex>"
    )
    return Response(content=xml, media_type="text/xml; charset=utf-8")


@app.get("/sitemap-main.xml")
async def sitemap_main():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "    <url><loc>https://gochilly.fun/</loc><priority>1.0</priority></url>\n"
        "    <url><loc>https://gochilly.fun/catalog</loc><priority>0.9</priority></url>\n"
        "    <url><loc>https://gochilly.fun/random</loc><priority>0.6</priority></url>\n"
        "    <url><loc>https://gochilly.fun/support</loc><priority>0.5</priority></url>\n"
        "</urlset>"
    )
    return Response(content=xml, media_type="text/xml; charset=utf-8")


@app.get("/sitemap-anime.xml")
async def sitemap_anime(request: Request):
    current_time = time.time()
    if SITEMAP_CACHE["xml"] and (current_time - SITEMAP_CACHE["time"] < SITEMAP_TTL):
        return Response(
            content=SITEMAP_CACHE["xml"], media_type="text/xml; charset=utf-8"
        )

    # Используем пул соединений PostgreSQL вместо aiosqlite
    async with request.app.state.pool.acquire() as db:
        # fetch() возвращает список записей, работаем с ними так же, как с Row
        rows = await db.fetch("SELECT id, slug FROM anime")

        urls = [
            f"<url><loc>https://gochilly.fun/anime/{r['slug'] if r['slug'] else r['id']}</loc><priority>0.7</priority></url>"
            for r in rows
        ]

        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'    {"".join(urls)}\n'
            "</urlset>"
        )

        SITEMAP_CACHE["xml"], SITEMAP_CACHE["time"] = xml_content, current_time
        return Response(content=xml_content, media_type="text/xml; charset=utf-8")


@app.get("/robots.txt")
async def robots():
    content = "User-agent: *\nAllow: /\n\nSitemap: https://gochilly.fun/sitemap.xml"
    return Response(content=content, media_type="text/plain")
