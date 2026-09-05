import os
import shutil
from datetime import datetime

DB_PATH = "hestia.db"
BACKUP_DIR = "backups"

def backup_db():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' does not exist. Run the chat first to create it.")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"hestia_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    shutil.copy2(DB_PATH, backup_path)
    print(f"Successfully backed up '{DB_PATH}' to '{backup_path}'")

if __name__ == "__main__":
    backup_db()
