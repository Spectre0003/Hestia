# Walkthrough: Stage 3 (v0.3) — Conversation Persistence

I've completed the implementation of Stage 3, bringing conversation persistence to Hestia.

## What was implemented

### 1. Database Layer (`db.py`)
I created a SQLite database interface that:
- Initializes `hestia.db` with a `messages` table.
- Handles generating session UUIDs.
- Saves incoming/outgoing messages with their roles and timestamps.
- Loads past messages for a given session.

### 2. Chat Updates (`chat.py`)
The chat script was upgraded to utilize the database:
- Added a `--resume` command-line argument.
- By default, it generates a new session and starts fresh.
- If run with `python chat.py --resume`, it finds the most recent session in the database, loads the chat history, and lets you continue right where you left off.
- User inputs and assistant replies are continuously written to the database during the chat loop.

### 3. Backup System (`backup.py`)
To fulfill the principle of scheduled backups, I created a backup script.
- It verifies if the database exists.
- It copies `hestia.db` to a `backups/` directory, appending a timestamp to the filename (e.g., `hestia_backup_20260905_203112.db`).

### 4. Documentation
- Updated `PROGRESS.md` to log today's working session and mark Stage 3 as complete.
- Updated the roadmap in `README.md` to show v0.3 as complete, with v0.4 (Long-term memory) up next.

## How to test

You can test this out by running:
1. `python chat.py` to start a new chat. Have a brief conversation, then type `exit`.
2. `python chat.py --resume` to resume the session. The model should remember what you just talked about.
3. `python backup.py` to ensure your database is safely backed up.
