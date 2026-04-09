import sqlite3

conn = sqlite3.connect("anime.db")
cursor = conn.cursor()

# Удаляем записи, у которых совпадает название, оставляя только ту, у которой ID меньше
cursor.execute(
    """
    DELETE FROM anime 
    WHERE id NOT IN (
        SELECT MIN(id) 
        FROM anime 
        GROUP BY title
    )
"""
)

conn.commit()
conn.close()
print("База очищена от дубликатов!")
