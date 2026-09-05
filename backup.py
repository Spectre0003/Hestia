"""
Hestia — backup script (Stage 3 / v0.3)

Copies the live database to a timestamped file under backups/. Run this
manually or on a schedule (Task Scheduler on Windows). Imports DB_PATH
from storage.py rather than hardcoding it again, so the two files can't
drift out of sync if the DB location ever changes.
"""

import os
import shutil
from datetime import datetime

import storage

BACKUP_DIR = "backups"


def backup_db():
    if not os.path.exists(storage.DB_PATH):
        print(f"Error: Database file '{storage.DB_PATH}' does not exist. Run chat.py first to create it.")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"hestia_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    shutil.copy2(storage.DB_PATH, backup_path)
    print(f"Successfully backed up '{storage.DB_PATH}' to '{backup_path}'")


if __name__ == "__main__":
    backup_db()