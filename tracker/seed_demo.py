#!/usr/bin/env python3
"""Seed demo data so the dashboard is useful before the tracker has run long."""
import sqlite3
import time
import random
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "sessions.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER,
    app_name        TEXT    NOT NULL,
    window_title    TEXT    DEFAULT '',
    category        TEXT    NOT NULL,
    keystrokes      INTEGER DEFAULT 0,
    duration_seconds INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_started_at ON sessions(started_at);
"""

DEMO_SESSIONS = [
    # (app, title, category, minutes, keystrokes_per_min)
    ("Visual Studio Code", "index.ts — my-project", "creating", 90, 120),
    ("Chrome",             "Hacker News",             "reading",  20, 5),
    ("Terminal",           "bash",                    "creating", 30, 60),
    ("Chrome",             "youtube.com — Lo-fi beats","video",   25, 2),
    ("Slack",              "# engineering",           "communication", 15, 40),
    ("Notion",             "Q2 Planning",             "creating", 45, 80),
    ("Chrome",             "MDN Web Docs — Array",    "reading",  18, 8),
    ("Arc",                "twitter.com",             "social",   12, 15),
    ("Chrome",             "netflix.com — Severance", "video",    40, 1),
    ("Claude",             "Debugging auth flow",     "ai",       35, 45),
    ("Obsidian",           "daily-notes.md",          "creating", 20, 90),
    ("Messages",           "Team",                    "communication", 8, 35),
    ("Chrome",             "reddit.com/r/programming","social",   10, 5),
]

def seed():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA)

    # Delete existing demo data for today
    today_start = int(time.time()) - (int(time.time()) % 86400)
    conn.execute("DELETE FROM sessions WHERE started_at >= ?", (today_start,))
    conn.commit()

    now = int(time.time())
    cursor = now - sum(m * 60 for _, _, _, m, _ in DEMO_SESSIONS)

    for app, title, category, minutes, kpm in DEMO_SESSIONS:
        duration = minutes * 60
        keystrokes = kpm * minutes + random.randint(-20, 20)
        conn.execute(
            "INSERT INTO sessions (started_at, ended_at, app_name, window_title, category, keystrokes, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cursor, cursor + duration, app, title, category, max(0, keystrokes), duration)
        )
        cursor += duration

    conn.commit()
    conn.close()
    print(f"✓ Seeded {len(DEMO_SESSIONS)} demo sessions into {DB_PATH}")

if __name__ == "__main__":
    seed()
