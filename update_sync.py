import asyncio
import httpx
import aiosqlite
import os
import re
from dotenv import load_dotenv

# Загрузка настроек
load_dotenv()
KODIK_TOKEN = os.getenv("KODIK_TOKEN")
DB_PATH = "/root/chilly_v2/anime.db"


def generate_slug(title, anime_id):
    symbols = ("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejzijklmnoprstufhzcss_y_eua")
    tr = {ord(a): ord(b) for a, b in zip(*symbols)}
    clean_name = title.lower().translate(tr)
    clean_name = re.sub(r"[^\w]+", "-", clean_name).strip("-")
    short_id = anime_id.split("-")[-1]
    return f"{clean_name}-{short_id}"


async def quick_update():
    # Используем лимит 50, чтобы снизить риск ошибки 500 от Kodik
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

            async with aiosqlite.connect(DB_PATH) as db:
                added_or_updated = 0

                for anime in results:
                    kp_id = anime.get("kinopoisk_id")

                    # Пропускаем без ID (Vibix их не покажет)
                    if not kp_id or str(kp_id).lower() == "none":
                        continue

                    m_data = anime.get("material_data", {})
                    slug = generate_slug(anime["title"], anime["id"])

                    # Подготавливаем данные
                    anime_data = (
                        anime["id"],
                        slug,
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

                    # ЛОГИКА БОРЬБЫ С ДУБЛЯМИ:
                    # 1. Сначала удаляем старую запись с таким же kinopoisk_id (если она есть)
                    # Это гарантирует, что "Человек-бензопила" всегда будет в одном экземпляре
                    await db.execute(
                        "DELETE FROM anime WHERE kinopoisk_id = ? AND title = ?",
                        (str(kp_id), anime["title"]),
                    )

                    # 2. Вставляем новую (самую свежую по версии Kodik)
                    await db.execute(
                        """
                        INSERT INTO anime (
                            id, slug, type, title, title_orig, other_title, year, 
                            episodes_count, kinopoisk_id, shikimori_id, imdb_id, 
                            rating_kp, rating_imdb, rating_shikimori, poster_url, 
                            description, genres, studios, player_link, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        anime_data,
                    )
                    added_or_updated += 1

                await db.commit()
                print(f"✅ Обработка завершена. Обновлено тайтлов: {added_or_updated}")

        except Exception as e:
            print(f"🔥 Критическая ошибка: {e}")


if __name__ == "__main__":
    asyncio.run(quick_update())
