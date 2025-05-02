import sqlite3

def add_rooms_table():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS rooms (
        token TEXT PRIMARY KEY,
        creator_id INTEGER,
        created_at REAL,
        FOREIGN KEY (creator_id) REFERENCES users (user_id)
    )""")
    conn.commit()
    conn.close()
    print("Table 'rooms' created successfully.")

if __name__ == "__main__":
    add_rooms_table()