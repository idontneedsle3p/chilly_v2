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
from datetime import datetime, timezone

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
CACHE_TTL = 1


def timeago(value):
    if not value:
        return ""
    try:
        # Kodik шлет дату типа "2026-04-28T12:00:00Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt

        # 1. Если прошло больше суток — показываем дату (день.месяц)
        if diff.days > 0:
            return dt.strftime("%d.%m")

        # 2. Если меньше часа — показываем минуты
        if diff.seconds < 3600:
            return f"{diff.seconds // 60} мин. назад"

        # 3. Если от 1 до 24 часов — показываем часы
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
        new_animes = CACHE["home_page"]["new"]
        popular_animes = CACHE["home_page"]["popular"]
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor_new = await db.execute(
                """
                    SELECT id, slug, title, poster_url, rating_shikimori, year, episodes_count, updated_at 
                    FROM anime 
                    WHERE rating_shikimori > 0 
                    GROUP BY title 
                    ORDER BY (year >= 2025) DESC, updated_at DESC 
                    LIMIT 48
                    """
            )
            new_animes = await cursor_new.fetchall()

            cursor_pop = await db.execute(
                "SELECT id, slug, title, poster_url, rating_shikimori, year, episodes_count FROM anime GROUP BY title ORDER BY rating_shikimori DESC LIMIT 48"
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
    q_lower = q.lower().strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, slug, title, title_orig, poster_url, rating_shikimori, year FROM anime GROUP BY title"
        )
        all_animes = await cursor.fetchall()

    exact_matches = []
    titles_map = {}
    for anime in all_animes:
        t, to = (anime["title"] or "").lower(), (anime["title_orig"] or "").lower()
        if t:
            titles_map[t] = anime
        if to:
            titles_map[to] = anime
        if q_lower in t or q_lower in to:
            exact_matches.append(anime)

    if not exact_matches:
        close_titles = difflib.get_close_matches(
            q_lower, titles_map.keys(), n=10, cutoff=0.55
        )
        results = [dict(titles_map[ct]) for ct in close_titles]
    else:
        exact_matches.sort(key=lambda x: x["rating_shikimori"] or 0, reverse=True)
        results = [dict(a) for a in exact_matches[:7]]
    return results


@app.get("/search")
async def search_anime(request: Request, q: str = Query(..., min_length=1)):
    """Поиск"""

    q_lower = q.lower().strip()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT id, slug, title, title_orig, poster_url, rating_shikimori, year FROM anime GROUP BY title"
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
        close_titles = difflib.get_close_matches(
            q_lower, titles_map.keys(), n=20, cutoff=0.55
        )

        seen_ids = set()
        for ct in close_titles:
            anime = titles_map[ct]
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
        "Триллер",
    ]

    animes = []
    if genre:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, slug, title, poster_url, rating_shikimori, year, episodes_count FROM anime WHERE genres LIKE ? LIMIT 40",
                (f"%{genre}%",),
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
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, slug FROM anime ORDER BY RANDOM() LIMIT 1"
        )
        anime = await cursor.fetchone()
        if anime:
            target = anime["slug"] if anime["slug"] else anime["id"]
            return RedirectResponse(url=f"/anime/{target}")
    return RedirectResponse(url="/")


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
    """Ситмап для статических страниц"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "    <url><loc>https://gochilly.fun/</loc><priority>1.0</priority></url>\n"
        "    <url><loc>https://gochilly.fun/catalog</loc><priority>0.9</priority></url>\n"
        "    <url><loc>https://gochilly.fun/random</loc><priority>0.6</priority></url>\n"
        "</urlset>"
    )
    return Response(content=xml, media_type="text/xml; charset=utf-8")


@app.get("/sitemap-anime.xml")
async def sitemap_anime():
    current_time = time.time()
    if SITEMAP_CACHE["xml"] and (current_time - SITEMAP_CACHE["time"] < SITEMAP_TTL):
        return Response(
            content=SITEMAP_CACHE["xml"], media_type="text/xml; charset=utf-8"
        )

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, slug FROM anime")
        rows = await cursor.fetchall()

        urls = [
            f"<url><loc>https://gochilly.fun/anime/{r['slug'] if r['slug'] else r['id']}</loc><priority>0.7</priority></url>"
            for r in rows
        ]
        xml_content = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n    {"".join(urls)}\n</urlset>'
        SITEMAP_CACHE["xml"], SITEMAP_CACHE["time"] = xml_content, current_time
        return Response(content=xml_content, media_type="text/xml; charset=utf-8")


@app.get("/robots.txt")
async def robots():
    content = "User-agent: *\nAllow: /\n\nSitemap: https://gochilly.fun/sitemap.xml"
    return Response(content=content, media_type="text/plain")


@app.get("/anime/{identifier}")
async def get_anime_page(request: Request, identifier: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        curr = await db.execute(
            "SELECT * FROM anime WHERE slug = ? OR id = ?", (identifier, identifier)
        )
        anime = await curr.fetchone()

        if not anime:
            raise HTTPException(status_code=404)

        if identifier == anime["id"] and anime["slug"]:
            return RedirectResponse(url=f"/anime/{anime['slug']}", status_code=301)

        genres_list = (
            [g.strip() for g in anime["genres"].split(",")] if anime["genres"] else []
        )
        stop_words = {"аниме", "мультфильм", "короткометражка", "сериал"}
        target_genres = [g for g in genres_list if g.lower() not in stop_words][:3]
        current_studio = (
            anime["studios"].split(",")[0].strip() if anime["studios"] else ""
        )
        current_type = anime["type"]
        kp_id = anime["kinopoisk_id"]

        similar_animes = []
        if target_genres:
            where_genres = " OR ".join(["genres LIKE ?" for _ in target_genres])
            weight_genres = " + ".join(
                [f"(CASE WHEN genres LIKE ? THEN 1 ELSE 0 END)" for _ in target_genres]
            )

            params = [f"%{g}%" for g in target_genres] + [
                f"%{g}%" for g in target_genres
            ]
            params.extend(
                [
                    f"%{current_studio}%" if current_studio else "",
                    current_type,
                    anime["id"],
                    kp_id,
                ]
            )

            query = f"""
                SELECT id, slug, title, poster_url, rating_shikimori, year,
                (({weight_genres}) + 
                 (CASE WHEN studios LIKE ? THEN 2 ELSE 0 END) + 
                 (CASE WHEN type = ? THEN 1 ELSE 0 END)) as weight
                FROM anime 
                WHERE ({where_genres}) 
                AND id != ? 
                AND (kinopoisk_id IS NULL OR kinopoisk_id != ?)
                GROUP BY COALESCE(kinopoisk_id, id) 
                ORDER BY weight DESC, rating_shikimori DESC
                LIMIT 24
            """
            cursor_sim = await db.execute(query, params)
            similar_animes = await cursor_sim.fetchall()

        cursor_seasons = await db.execute(
            "SELECT id, slug, title, player_link FROM anime WHERE kinopoisk_id = ? ORDER BY year ASC, title ASC",
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
