import sqlite3
import requests
import time
from tqdm import tqdm

DB_PATH = "anime.db"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Теперь мы берем ВООБЩЕ ВСЕ аниме, у которых есть shikimori_id
cursor.execute(
    """
    SELECT id, shikimori_id, title, poster_url 
    FROM anime 
    WHERE shikimori_id IS NOT NULL AND shikimori_id != 'None'
"""
)

all_anime = cursor.fetchall()
print(f"🕵️ Всего аниме для проверки: {len(all_anime)}")

HEADERS = {"User-Agent": "AnimeWebUpdater/1.0"}
success_count = 0


def is_image_broken(url):
    """Проверяет, жива ли ссылка, не скачивая саму картинку."""
    if not url or url == "None":
        return True
    try:
        # Используем HEAD (быстрый запрос только заголовков)
        response = requests.head(url, timeout=5, headers=HEADERS)
        # Если статус 400, 403, 404 и т.д. — ссылка битая
        return response.status_code >= 400
    except requests.RequestException:
        # Если домен вообще не отвечает (сайт удален)
        return True


# Начинаем проверку
for db_id, shiki_id, title, poster_url in tqdm(all_anime, desc="Проверка постеров"):

    # 1. Сначала проверяем, жива ли текущая ссылка
    if is_image_broken(poster_url):
        tqdm.write(f"🔍 Битая ссылка (или её нет) у: {title}. Ищем замену...")

        try:
            # 2. Ссылка мертва. Идем на Шикимори за новой
            url = f"https://shikimori.one/api/animes/{shiki_id}"
            api_response = requests.get(url, headers=HEADERS)

            if api_response.status_code == 200:
                data = api_response.json()
                image_path = data.get("image", {}).get("original")

                if image_path:
                    full_poster_url = f"https://shikimori.one{image_path}"

                    # 3. Обновляем базу
                    cursor.execute(
                        "UPDATE anime SET poster_url = ? WHERE id = ?",
                        (full_poster_url, db_id),
                    )
                    conn.commit()
                    success_count += 1
                    tqdm.write(f"✅ Успешно обновлен: {full_poster_url}")
                else:
                    tqdm.write(f"⚠️ У {title} нет картинки на Шикимори")
            else:
                tqdm.write(
                    f"❌ Ошибка API для {title} (Код: {api_response.status_code})"
                )

            # Делаем паузу только если дергали API Шикимори, чтобы не забанили
            time.sleep(0.3)

        except Exception as e:
            tqdm.write(f"🚨 Ошибка при обработке {title}: {e}")

conn.close()
print(f"\n🎉 Готово! Восстановлено битых постеров: {success_count}")
