import sqlite3
import uuid
from datetime import datetime

DB_PATH = "hestia.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (session_id, role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def load_messages(session_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, content FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    ''', (session_id,))
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for row in rows:
        messages.append({"role": row[0], "content": row[1]})
    return messages

def get_latest_session_id():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT session_id FROM messages
        ORDER BY timestamp DESC
        LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return row[0]
    return None

def create_new_session_id():
    return str(uuid.uuid4())

if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
