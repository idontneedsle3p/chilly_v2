import asyncio
import httpx
import asyncpg
import os
import re
from dotenv import load_dotenv

# Загрузка настроек
load_dotenv()
KODIK_TOKEN = os.getenv("KODIK_TOKEN")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://admin:123@localhost:5432/anime_db"
)


def generate_slug(title, anime_id):
    # Логика генерации слага остается без изменений
    symbols = ("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejzijklmnoprstufhzcss-y-eua")
    tr = {ord(a): ord(b) for a, b in zip(*symbols)}
    clean_name = title.lower().translate(tr)
    clean_name = re.sub(r"[^a-z0-9]+", "-", clean_name).strip("-")
    clean_name = re.sub(r"-+", "-", clean_name)
    short_id = anime_id.split("-")[-1]
    return f"{clean_name}-{short_id}"


async def quick_update():
    url = "https://kodik-api.com/list"
    params = {
        "token": KODIK_TOKEN,
        "types": "anime,anime-serial",
        "with_material_data": "true",
        "limit": 50,
        "sort": "updated_at",
        "order": "desc",
    }

    async with httpx.AsyncClient() as client:
        try:
            print("📡 Запрос обновлений у Kodik...")
            response = await client.get(url, params=params, timeout=25.0)

            if response.status_code != 200:
                print(f"🛑 Kodik API error: {response.status_code}")
                return

            data = response.json()
            results = data.get("results", [])

            if not results:
                print("📭 Новых обновлений пока нет.")
                return

            # Подключаемся к PostgreSQL
            conn = await asyncpg.connect(DATABASE_URL)
            added_or_updated = 0

            # Используем транзакцию для надежности
            async with conn.transaction():
                for anime in results:
                    kp_id = anime.get("kinopoisk_id")
                    if not kp_id or str(kp_id).lower() == "none":
                        continue

                    # 1. Ищем существующее аниме (в Postgres используем $1, $2)
                    row = await conn.fetchrow(
                        "SELECT episodes_count FROM anime WHERE kinopoisk_id = $1 AND title = $2",
                        str(kp_id),
                        anime["title"],
                    )

                    new_episodes = int(anime.get("episodes_count", 1))
                    if anime["type"] == "anime" and new_episodes == 0:
                        new_episodes = 1

                    if row:
                        existing_episodes = row["episodes_count"]

                        if new_episodes > existing_episodes:
                            m_data = anime.get("material_data", {})
                            slug = generate_slug(anime["title"], anime["id"])

                            await conn.execute(
                                """
                                UPDATE anime SET 
                                    slug = $1, episodes_count = $2, updated_at = $3, 
                                    player_link = $4, rating_kp = $5, rating_imdb = $6, 
                                    rating_shikimori = $7, poster_url = $8
                                WHERE kinopoisk_id = $9 AND title = $10
                                """,
                                slug,
                                new_episodes,
                                anime.get("updated_at"),
                                anime.get("link"),
                                float(m_data.get("kinopoisk_rating", 0.0)),
                                float(m_data.get("imdb_rating", 0.0)),
                                float(m_data.get("shikimori_rating", 0.0)),
                                m_data.get("poster_url"),
                                str(kp_id),
                                anime["title"],
                            )
                            added_or_updated += 1
                    else:
                        # 2. Вставка нового аниме
                        m_data = anime.get("material_data", {})
                        slug = generate_slug(anime["title"], anime["id"])

                        # Принудительно приводим к типам, которые ждет Postgres
                        await conn.execute(
                            """
                            INSERT INTO anime (
                                id, slug, type, title, title_orig, other_title, year, 
                                episodes_count, kinopoisk_id, shikimori_id, imdb_id, 
                                rating_kp, rating_imdb, rating_shikimori, poster_url, 
                                description, genres, studios, player_link, updated_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
                            """,
                            str(anime["id"]),
                            slug,
                            anime["type"],
                            anime["title"],
                            anime.get("title_orig"),
                            anime.get("other_title"),
                            int(anime.get("year")) if anime.get("year") else None,
                            new_episodes,
                            str(kp_id),
                            str(anime.get("shikimori_id", "None")),
                            str(anime.get("imdb_id", "None")),
                            float(m_data.get("kinopoisk_rating", 0.0)),
                            float(m_data.get("imdb_rating", 0.0)),
                            float(m_data.get("shikimori_rating", 0.0)),
                            m_data.get("poster_url"),
                            m_data.get("description"),
                            (
                                ", ".join(m_data.get("all_genres", []))
                                if m_data.get("all_genres")
                                else ""
                            ),
                            (
                                ", ".join(m_data.get("anime_studios", []))
                                if m_data.get("anime_studios")
                                else ""
                            ),
                            anime.get("link"),
                            anime.get("updated_at"),
                        )
                        added_or_updated += 1

            await conn.close()
            print(f"✅ Обработка завершена. Обновлено тайтлов: {added_or_updated}")

        except Exception as e:
            print(f"🔥 Критическая ошибка: {e}")


if __name__ == "__main__":
    asyncio.run(quick_update())
