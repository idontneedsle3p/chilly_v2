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
    symbols = ("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejzijklmnoprstufhzcss-y-eua")
    tr = {ord(a): ord(b) for a, b in zip(*symbols)}
    clean_name = title.lower().translate(tr)
    clean_name = re.sub(r"[^a-z0-9]+", "-", clean_name).strip("-")
    clean_name = re.sub(r"-+", "-", clean_name)
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
                    if not kp_id or str(kp_id).lower() == "none":
                        continue

                    # 1. Ищем, есть ли уже это аниме в базе
                    cursor = await db.execute(
                        "SELECT episodes_count FROM anime WHERE kinopoisk_id = ? AND title = ?",
                        (str(kp_id), anime["title"]),
                    )
                    row = await cursor.fetchone()

                    new_episodes = anime.get("episodes_count", 1)
                    if anime["type"] == "anime" and new_episodes == 0:
                        new_episodes = 1

                    if row:
                        existing_episodes = row[0]

                        # Обновляем ТОЛЬКО если серий стало больше
                        if new_episodes > existing_episodes:
                            m_data = anime.get("material_data", {})
                            slug = generate_slug(anime["title"], anime["id"])

                            await db.execute(
                                """
                                UPDATE anime SET 
                                    slug = ?, episodes_count = ?, updated_at = ?, 
                                    player_link = ?, rating_kp = ?, rating_imdb = ?, 
                                    rating_shikimori = ?, poster_url = ?
                                WHERE kinopoisk_id = ? AND title = ?
                                """,
                                (
                                    slug,
                                    new_episodes,
                                    anime.get("updated_at"),
                                    anime.get("link"),
                                    m_data.get("kinopoisk_rating", 0.0),
                                    m_data.get("imdb_rating", 0.0),
                                    m_data.get("shikimori_rating", 0.0),
                                    m_data.get("poster_url"),
                                    str(kp_id),
                                    anime["title"],
                                ),
                            )
                            added_or_updated += 1
                        else:
                            # Пропускаем, если серий столько же. updated_at в базе не меняется!
                            continue
                    else:
                        # 2. Вставка нового аниме
                        m_data = anime.get("material_data", {})
                        slug = generate_slug(anime["title"], anime["id"])

                        anime_data = (
                            anime["id"],
                            slug,
                            anime["type"],
                            anime["title"],
                            anime.get("title_orig"),
                            anime.get("other_title"),
                            anime.get("year"),
                            new_episodes,
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

                        await db.execute(
                            "INSERT INTO anime (...) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            anime_data,
                        )
                        added_or_updated += 1

                # Сохраняем все изменения пачкой после цикла

                await db.commit()
                print(f"✅ Обработка завершена. Обновлено тайтлов: {added_or_updated}")

        except Exception as e:
            print(f"🔥 Критическая ошибка: {e}")


if __name__ == "__main__":
    asyncio.run(quick_update())
