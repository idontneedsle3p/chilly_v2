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


app = FastAPI(lifespan=lifespan)
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


def clean_title(title: str) -> str:
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
                SELECT DISTINCT ON (title) id, slug, title, poster_url, rating_shikimori, year, episodes_count 
                FROM anime 
                ORDER BY title, rating_shikimori DESC
            ) AS sub
            ORDER BY rating_shikimori DESC 
            LIMIT 48
        """)

    data = {"new_animes": new_animes, "popular_animes": popular_animes}
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

        # Поиск похожих
        similar_animes = []
        if genres:
            # Упрощенная логика весов для Postgres
            similar_animes = await db.fetch(
                """
                SELECT DISTINCT ON (title) id, slug, title, poster_url, rating_shikimori, year
                FROM anime 
                WHERE genres ILIKE $1 AND id != $2
                ORDER BY title, rating_shikimori DESC LIMIT 24
            """,
                f"%{genres[0]}%",
                anime["id"],
            )

        seasons = await db.fetch(
            """
            SELECT id, slug, title, player_link 
            FROM anime WHERE kinopoisk_id = $1 
            ORDER BY year ASC, title ASC
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


@app.get("/sitemap.xml")
async def sitemap_index():
    """Главный индексный файл"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "    <sitemap>\n"
        "        <loc>https://gochilly.fun/sitemap-main.xml</loc>\n"
        "        <lastmod>2026-04-27</lastmod>\n"
        "    </sitemap>\n"
        "    <sitemap>\n"
        "        <loc>https://gochilly.fun/sitemap-anime.xml</loc>\n"
        "        <lastmod>2026-04-27</lastmod>\n"
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
