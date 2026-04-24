import asyncio
import httpx
import aiosqlite
import os
from dotenv import load_dotenv

load_dotenv()
KODIK_TOKEN = os.getenv("KODIK_TOKEN")
DB_PATH = "/root/chilly_v2/anime.db"  # Полный путь для Cron


async def quick_update():
    url = "https://kodik-api.com/list"
    params = {
        "token": KODIK_TOKEN,
        "types": "anime,anime-serial",
        "with_material_data": "true",
        "limit": 1,
        "sort": "updated_at",
        "order": "desc",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=20.0)
            if response.status_code != 200:
                print(f"🛑 Kodik API error: {response.status_code}")
                return

            data = response.json()
            results = data.get("results", [])

            async with aiosqlite.connect(DB_PATH) as db:
                added = 0
                for anime in results:
                    kp_id = anime.get("kinopoisk_id")
                    # Пропускаем, если нет Кинопоиск ID (важно для Vibix)
                    if not kp_id or str(kp_id).lower() == "none":
                        continue

                    m_data = anime.get("material_data", {})
                    anime_data = (
                        anime["id"],
                        anime["type"],
                        anime["title"],
                        anime.get("title_orig"),
                        anime.get("other_title"),
                        anime.get("year"),
                        anime.get("episodes_count", 1),
                        str(kp_id),
                        str(anime.get("shikimori_id", "None")),
                        str(anime.get("imdb_id", "None")),
                        m_data.get("kinopoisk_rating", 0.0),
                        m_data.get("imdb_rating", 0.0),
                        m_data.get("shikimori_rating", 0.0),
                        m_data.get("poster_url"),
                        m_data.get("description"),
                        ", ".join(m_data.get("all_genres", [])),
                        ", ".join(m_data.get("anime_studios", [])),
                        anime.get("link"),
                        anime.get("updated_at"),
                    )

                    await db.execute(
                        """
                        INSERT INTO anime (
                            id, type, title, title_orig, other_title, year, 
                            episodes_count, kinopoisk_id, shikimori_id, imdb_id, 
                            rating_kp, rating_imdb, rating_shikimori, poster_url, 
                            description, genres, studios, player_link, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            episodes_count = excluded.episodes_count,
                            rating_kp = excluded.rating_kp,
                            rating_shikimori = excluded.rating_shikimori,
                            updated_at = excluded.updated_at,
                            poster_url = excluded.poster_url,
                            player_link = excluded.player_link
                    """,
                        anime_data,
                    )
                    added += 1

                await db.commit()
                print(f"✅ Успешно проверено 100 тайтлов. Обновлено/добавлено: {added}")

        except Exception as e:
            print(f"🔥 Ошибка при авто-обновлении: {e}")


if __name__ == "__main__":
    asyncio.run(quick_update())
