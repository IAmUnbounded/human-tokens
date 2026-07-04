#!/usr/bin/env python3
"""
Human Token Tracker.

Records active app/window, keystroke-derived output, time-based media input,
and visible browser text when the active browser allows AppleScript capture.

Run foreground:
  python3 tracker/tracker.py

Run background:
  ./scripts/start_background.sh

macOS permissions that improve capture:
  Accessibility    - keystroke counting
  Screen Recording - active window titles
  Automation       - browser URL/text via AppleScript
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from AppKit import NSWorkspace

try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowLayer,
        kCGWindowListOptionOnScreenOnly,
        kCGWindowName,
        kCGWindowOwnerName,
    )

    SCREEN_RECORDING = True
except Exception:
    SCREEN_RECORDING = False

try:
    from Quartz import (
        CGEventSourceSecondsSinceLastEventType,
        kCGAnyInputEventType,
        kCGEventSourceStateHIDSystemState,
    )

    SYSTEM_IDLE_AVAILABLE = True
except Exception:
    SYSTEM_IDLE_AVAILABLE = False


def system_idle_seconds() -> float:
    """Seconds since the last system-wide mouse/keyboard/scroll event.

    Unlike keystroke counting, this needs no Accessibility permission and
    reflects any input device, so it also catches mouse-only activity
    (scrolling while reading, etc).
    """
    if not SYSTEM_IDLE_AVAILABLE:
        return 0.0
    try:
        return float(
            CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
            )
        )
    except Exception:
        return 0.0

KEYSTROKE_TRACKING = False
try:
    from pynput import keyboard as kb_module

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        _test = kb_module.Listener(on_press=lambda k: None)
        _test.start()
        time.sleep(0.15)
        _test.stop()

    KEYSTROKE_TRACKING = "not trusted" not in buf.getvalue().lower()
except Exception:
    kb_module = None


ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "sessions.db"
REPORT_DIR = ROOT / "reports"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       INTEGER NOT NULL,
    ended_at         INTEGER,
    app_name         TEXT    NOT NULL,
    window_title     TEXT    DEFAULT '',
    category         TEXT    NOT NULL,
    keystrokes       INTEGER DEFAULT 0,
    duration_seconds INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_started_at ON sessions(started_at);

CREATE TABLE IF NOT EXISTS captures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER,
    captured_at   INTEGER NOT NULL,
    app_name      TEXT NOT NULL,
    window_title  TEXT DEFAULT '',
    source_url    TEXT DEFAULT '',
    source_host   TEXT DEFAULT '',
    category      TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    text_hash     TEXT DEFAULT '',
    text_excerpt  TEXT DEFAULT '',
    status        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_captures_at ON captures(captured_at);
CREATE INDEX IF NOT EXISTS idx_captures_session ON captures(session_id);

CREATE TABLE IF NOT EXISTS seen_text (
    day           TEXT NOT NULL,
    text_hash     TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    source_url    TEXT DEFAULT '',
    text_excerpt  TEXT DEFAULT '',
    token_count   INTEGER DEFAULT 0,
    PRIMARY KEY (day, text_hash)
);
CREATE INDEX IF NOT EXISTS idx_seen_text_day ON seen_text(day);
"""

SESSION_MIGRATIONS = {
    "source_url": "TEXT DEFAULT ''",
    "source_host": "TEXT DEFAULT ''",
    "input_tokens": "INTEGER DEFAULT 0",
    "output_tokens": "INTEGER DEFAULT 0",
    "text_input_tokens": "INTEGER DEFAULT 0",
    "rate_input_tokens": "INTEGER DEFAULT 0",
    "capture_count": "INTEGER DEFAULT 0",
    "last_capture_status": "TEXT DEFAULT ''",
}


# category -> (output_tokens_per_min fallback, input_tokens_per_min fallback)
TOKEN_RATES: dict[str, tuple[int, int]] = {
    "creating": (0, 5),
    "reading": (0, 200),
    "ai": (0, 150),
    "video": (0, 180),
    "social": (0, 120),
    "communication": (0, 75),
    "other": (0, 0),
    "idle": (0, 0),
}

AI_APPS = {
    "Claude",
    "ChatGPT",
    "Ollama",
    "LM Studio",
    "GitHub Copilot",
    "Copilot",
}

AI_HOSTS = {
    "claude.ai",
    "chatgpt.com",
    "chat.openai.com",
    "gemini.google.com",
    "perplexity.ai",
    "copilot.microsoft.com",
    "poe.com",
    "grok.com",
}

CREATING_APPS = {
    "Code",
    "Visual Studio Code",
    "Cursor",
    "Windsurf",
    "Xcode",
    "Android Studio",
    "Sublime Text",
    "TextMate",
    "BBEdit",
    "Vim",
    "Neovim",
    "Emacs",
    "MacVim",
    "IntelliJ IDEA",
    "PyCharm",
    "WebStorm",
    "GoLand",
    "RubyMine",
    "CLion",
    "DataGrip",
    "Rider",
    "Fleet",
    "Terminal",
    "iTerm2",
    "Warp",
    "Hyper",
    "Ghostty",
    "Alacritty",
    "kitty",
    "Pages",
    "Microsoft Word",
    "LibreOffice Writer",
    "Notion",
    "Obsidian",
    "Bear",
    "Ulysses",
    "iA Writer",
    "Typora",
    "Drafts",
    "Numbers",
    "Microsoft Excel",
    "Keynote",
    "Microsoft PowerPoint",
    "Photoshop",
    "Illustrator",
    "Affinity Designer",
    "Affinity Photo",
    "Figma",
    "Sketch",
    "Logic Pro",
    "GarageBand",
    "Ableton Live",
    "Final Cut Pro",
    "DaVinci Resolve",
    "Premiere Pro",
}

BROWSER_APPS = {
    "Safari",
    "Chrome",
    "Google Chrome",
    "Chromium",
    "Google Chrome for Testing",
    "Firefox",
    "Arc",
    "Brave Browser",
    "Microsoft Edge",
    "Opera",
    "Vivaldi",
}

APPLESCRIPT_BROWSER_NAME = {
    "Chrome": "Google Chrome",
    "Google Chrome": "Google Chrome",
    "Chromium": "Chromium",
    "Google Chrome for Testing": "Google Chrome for Testing",
    "Brave Browser": "Brave Browser",
    "Microsoft Edge": "Microsoft Edge",
    "Arc": "Arc",
    "Vivaldi": "Vivaldi",
    "Opera": "Opera",
    "Safari": "Safari",
}

CONSUMING_APPS = {
    "YouTube": "video",
    "Netflix": "video",
    "Twitch": "video",
    "VLC": "video",
    "IINA": "video",
    "QuickTime Player": "video",
    "Infuse": "video",
    "Plex": "video",
    "Twitter": "social",
    "X": "social",
    "Twitterrific": "social",
    "Instagram": "social",
    "TikTok": "social",
    "Reddit": "social",
    "LinkedIn": "social",
    "Facebook": "social",
    "Slack": "communication",
    "Discord": "communication",
    "Telegram": "communication",
    "WhatsApp": "communication",
    "Messages": "communication",
    "Mail": "communication",
    "Spark": "communication",
    "Mimestream": "communication",
    "Zoom": "communication",
    "Microsoft Teams": "communication",
    "Spotify": "other",
    "Apple Music": "other",
    "Podcasts": "other",
    "Overcast": "other",
}

VIDEO_HOSTS = {
    "youtube.com",
    "youtu.be",
    "netflix.com",
    "twitch.tv",
    "hulu.com",
    "disneyplus.com",
    "primevideo.com",
    "vimeo.com",
    "dailymotion.com",
    "crunchyroll.com",
    "peacocktv.com",
}

SOCIAL_HOSTS = {
    "twitter.com",
    "x.com",
    "reddit.com",
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "linkedin.com",
    "news.ycombinator.com",
    "threads.net",
}

VISUAL_SOCIAL_HOSTS = {
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "threads.net",
}

VIDEO_KEYWORDS = [
    "youtube",
    "netflix",
    "twitch",
    "hulu",
    "disney+",
    "prime video",
    "vimeo",
    "dailymotion",
    "crunchyroll",
    "peacock",
]

SOCIAL_KEYWORDS = [
    "twitter.com",
    "x.com",
    "reddit.com",
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "tiktok.com",
    "hacker news",
    "news.ycombinator.com",
    "threads.net",
]

NAV_TEXT = {
    "home",
    "search",
    "notifications",
    "messages",
    "profile",
    "more",
    "for you",
    "following",
    "explore",
    "reels",
    "shorts",
    "like",
    "reply",
    "share",
    "subscribe",
    "log in",
    "sign in",
    "sign up",
}

POLL_INTERVAL = 2
CAPTURE_INTERVAL = 8
CAPTURE_STALE_SECONDS = 30
IDLE_THRESHOLD = 300
FLUSH_INTERVAL = 10
MAX_BROWSER_TEXT_CHARS = 14_000


VISIBLE_TEXT_JS = " ".join(
    line.strip()
    for line in r"""
(() => {
  const MAX_CHARS = 14000;
  const root = document.body || document.documentElement;
  const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
  const hiddenTags = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT", "SELECT", "OPTION"]);
  const lines = [];
  const seen = new Set();
  let chars = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const raw = node.nodeValue || "";
      const value = raw.replace(/\s+/g, " ").trim();
      if (value.length < 2) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent || hiddenTags.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      const style = window.getComputedStyle(parent);
      if (!style || style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  let node;
  while ((node = walker.nextNode())) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects());
    const visible = rects.some((r) =>
      r.width > 1 && r.height > 1 &&
      r.bottom >= 0 && r.right >= 0 &&
      r.top <= viewportH && r.left <= viewportW
    );
    range.detach();
    if (!visible) continue;
    const value = (node.nodeValue || "").replace(/\s+/g, " ").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    lines.push(value);
    chars += value.length + 1;
    if (chars >= MAX_CHARS) break;
  }
  return JSON.stringify({
    url: location.href,
    title: document.title,
    text: lines.join("\n").slice(0, MAX_CHARS),
    viewport: { width: viewportW, height: viewportH },
    capturedAt: Date.now()
  });
})()
""".splitlines()
)

META_JS = "JSON.stringify({url: location.href, title: document.title, text: ''})"

VIDEO_PLAYING_JS = (
    "JSON.stringify({playing: Array.from(document.querySelectorAll('video, audio'))"
    ".some(m => !m.paused && !m.ended && m.currentTime > 0)})"
)


_ws = NSWorkspace.sharedWorkspace()
_keystroke_count = 0
_keystroke_lock = threading.Lock()


if KEYSTROKE_TRACKING and kb_module is not None:

    def _on_press(_key):
        global _keystroke_count
        with _keystroke_lock:
            _keystroke_count += 1

    _listener = kb_module.Listener(on_press=_on_press, suppress=False)
    _listener.daemon = True
    _listener.start()


@dataclass
class BrowserContext:
    url: str = ""
    title: str = ""
    text: str = ""
    status: str = "not-browser"
    captured_at: int = 0


@dataclass
class Session:
    app: str
    title: str
    category: str
    source_url: str
    source_host: str
    started_at: int
    keystrokes: int = 0
    duration: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    text_input_tokens: int = 0
    rate_input_tokens: int = 0
    capture_count: int = 0
    last_capture_status: str = ""
    db_id: int | None = None
    idle_secs: int = 0
    last_capture_at: int = 0
    last_text_capture_ok_at: int = 0
    output_float: float = 0.0
    rate_input_float: float = 0.0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for name, definition in SESSION_MIGRATIONS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
    conn.commit()


def local_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def start_of_day_ts(day: str) -> int:
    parsed = dt.datetime.strptime(day, "%Y-%m-%d")
    return int(parsed.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def previous_day(day: str) -> str:
    parsed = dt.datetime.strptime(day, "%Y-%m-%d").date()
    return (parsed - dt.timedelta(days=1)).strftime("%Y-%m-%d")


def pop_keystrokes() -> int:
    global _keystroke_count
    with _keystroke_lock:
        count = _keystroke_count
        _keystroke_count = 0
        return count


def host_from_url(url: str) -> str:
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def host_matches(host: str, candidates: set[str]) -> bool:
    return any(host == candidate or host.endswith("." + candidate) for candidate in candidates)


def get_active_window() -> tuple[str, str]:
    try:
        app = _ws.frontmostApplication()
        app_name = app.localizedName() or "unknown"

        title = ""
        if SCREEN_RECORDING:
            try:
                windows = CGWindowListCopyWindowInfo(
                    kCGWindowListOptionOnScreenOnly, kCGNullWindowID
                )
                for window in windows:
                    if (
                        window.get(kCGWindowOwnerName) == app_name
                        and window.get(kCGWindowLayer) == 0
                    ):
                        title = window.get(kCGWindowName) or ""
                        break
            except Exception:
                pass

        return app_name, title
    except Exception:
        return "unknown", ""


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_osascript(script: str, timeout: float = 4.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["osascript"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, f"osascript-error:{type(exc).__name__}"

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        return False, msg[:240] or f"exit-{proc.returncode}"
    return True, proc.stdout.strip()


def browser_script(app_name: str, include_text: bool) -> str | None:
    script_app = APPLESCRIPT_BROWSER_NAME.get(app_name)
    if not script_app:
        return None

    js = VISIBLE_TEXT_JS if include_text else META_JS
    js_string = applescript_string(js[:MAX_BROWSER_TEXT_CHARS * 2])

    if script_app == "Safari":
        return f"""
set jsCode to {js_string}
tell application "Safari"
    if not (exists front window) then return ""
    set theTab to current tab of front window
    return do JavaScript jsCode in theTab
end tell
"""

    return f"""
set jsCode to {js_string}
tell application "{script_app}"
    if not (exists front window) then return ""
    set theTab to active tab of front window
    return execute theTab javascript jsCode
end tell
"""


def browser_meta_fallback_script(app_name: str) -> str | None:
    script_app = APPLESCRIPT_BROWSER_NAME.get(app_name)
    if not script_app:
        return None
    if script_app == "Safari":
        return """
tell application "Safari"
    if not (exists front window) then return ""
    set theTab to current tab of front window
    return (URL of theTab) & linefeed & (name of theTab)
end tell
"""
    return f"""
tell application "{script_app}"
    if not (exists front window) then return ""
    set theTab to active tab of front window
    return (URL of theTab) & linefeed & (title of theTab)
end tell
"""


def classify_capture_error(output: str) -> str:
    if not output:
        return "capture-failed"
    lowered = output.lower()
    if "not authorized" in lowered or "not allowed" in lowered:
        return "automation-blocked"
    if "javascript" in lowered:
        return "javascript-blocked"
    return "capture-failed"


def capture_browser_context(app_name: str, include_text: bool = True) -> BrowserContext:
    if app_name not in BROWSER_APPS:
        return BrowserContext(status="not-browser", captured_at=int(time.time()))

    # Fetch URL/title first via the cheap, JS-free AppleScript path. This must
    # succeed independent of whether the browser allows JS-from-Apple-Events
    # or the heavier text-capture script times out, otherwise source_host
    # never populates and per-site breakdowns go empty.
    fallback = browser_meta_fallback_script(app_name)
    if not fallback:
        return BrowserContext(status="unsupported-browser", captured_at=int(time.time()))

    meta_ok, meta_output = run_osascript(fallback)
    now = int(time.time())
    if not (meta_ok and meta_output):
        return BrowserContext(status=classify_capture_error(meta_output), captured_at=now)

    lines = meta_output.splitlines()
    url = lines[0].strip() if lines else ""
    title = lines[1].strip() if len(lines) > 1 else ""

    if not include_text:
        return BrowserContext(url=url, title=title, status="meta-ok", captured_at=now)

    script = browser_script(app_name, include_text)
    if script:
        ok, output = run_osascript(script)
        if ok and output:
            try:
                parsed = json.loads(output)
                return BrowserContext(
                    url=str(parsed.get("url") or url),
                    title=str(parsed.get("title") or title),
                    text=str(parsed.get("text") or "")[:MAX_BROWSER_TEXT_CHARS],
                    status="text-ok",
                    captured_at=now,
                )
            except json.JSONDecodeError:
                pass
        text_status = classify_capture_error(output)
        return BrowserContext(
            url=url, title=title, text="", status=f"{text_status}+meta-only", captured_at=now
        )

    return BrowserContext(url=url, title=title, status="meta-only", captured_at=now)


def check_video_playing(app_name: str) -> bool | None:
    """True/False if we could ask the tab, None if we couldn't tell (e.g. JS blocked)."""
    script_app = APPLESCRIPT_BROWSER_NAME.get(app_name)
    if not script_app:
        return None

    js_string = applescript_string(VIDEO_PLAYING_JS)
    if script_app == "Safari":
        script = f"""
set jsCode to {js_string}
tell application "Safari"
    if not (exists front window) then return ""
    set theTab to current tab of front window
    return do JavaScript jsCode in theTab
end tell
"""
    else:
        script = f"""
set jsCode to {js_string}
tell application "{script_app}"
    if not (exists front window) then return ""
    set theTab to active tab of front window
    return execute theTab javascript jsCode
end tell
"""

    ok, output = run_osascript(script, timeout=3.0)
    if not ok or not output:
        return None
    try:
        return bool(json.loads(output).get("playing"))
    except json.JSONDecodeError:
        return None


def classify(app_name: str, window_title: str, source_url: str = "") -> str:
    host = host_from_url(source_url)
    if host:
        if host_matches(host, AI_HOSTS):
            return "ai"
        if host_matches(host, VIDEO_HOSTS):
            return "video"
        if host_matches(host, SOCIAL_HOSTS):
            return "social"

    if app_name in AI_APPS:
        return "ai"
    if app_name in CREATING_APPS:
        return "creating"
    if app_name in CONSUMING_APPS:
        return CONSUMING_APPS[app_name]
    if app_name in BROWSER_APPS:
        title = window_title.lower()
        if any(keyword in title for keyword in VIDEO_KEYWORDS):
            return "video"
        if any(keyword in title for keyword in SOCIAL_KEYWORDS):
            return "social"
        return "reading"
    return "other"


def should_capture_text(category: str, source_host: str) -> bool:
    if category in {"reading", "ai"}:
        return True
    if category == "social" and not host_matches(source_host, VISUAL_SOCIAL_HOSTS):
        return True
    return False


def should_apply_time_input(session: Session, now: int) -> bool:
    if session.category in {"video", "communication"}:
        return True
    if session.category in {"reading", "social", "ai"}:
        if session.app not in BROWSER_APPS:
            return True
        if not should_capture_text(session.category, session.source_host):
            return True
        return now - session.last_text_capture_ok_at > CAPTURE_STALE_SECONDS
    return False


def estimate_tokens(text: str) -> int:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return 0
    words = len(re.findall(r"\S+", cleaned))
    by_words = words * 1.33
    by_chars = len(cleaned) / 4
    return max(1, int(round(max(by_words, by_chars))))


def canonical_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned.casefold()


def visible_text_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        key = canonical_text(line)
        if key in seen or key in NAV_TEXT:
            continue
        if len(line) < 18 and len(line.split()) < 4:
            continue
        if re.fullmatch(r"[\W_0-9]+", line):
            continue
        seen.add(key)
        chunks.append(line)

    return chunks


def add_seen_text_tokens(
    conn: sqlite3.Connection,
    *,
    session: Session,
    captured_at: int,
    day: str,
    text: str,
    status: str,
) -> int:
    chunks = visible_text_chunks(text)
    if not chunks:
        insert_capture(
            conn,
            session=session,
            captured_at=captured_at,
            source_kind="browser_text",
            input_tokens=0,
            text_hash="",
            text_excerpt="",
            status=status,
        )
        return 0

    total_tokens = 0
    new_chunks: list[str] = []
    chunk_hashes: list[str] = []

    for chunk in chunks:
        normalized = canonical_text(chunk)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        token_count = estimate_tokens(chunk)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO seen_text
            (day, text_hash, first_seen_at, source_url, text_excerpt, token_count)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                digest,
                captured_at,
                session.source_url,
                chunk[:500],
                token_count,
            ),
        )
        if cur.rowcount:
            total_tokens += token_count
            new_chunks.append(chunk)
            chunk_hashes.append(digest)

    excerpt = " / ".join(new_chunks)[:700]
    capture_hash = hashlib.sha256("".join(chunk_hashes).encode("utf-8")).hexdigest()
    insert_capture(
        conn,
        session=session,
        captured_at=captured_at,
        source_kind="browser_text",
        input_tokens=total_tokens,
        text_hash=capture_hash,
        text_excerpt=excerpt,
        status=status,
    )
    return total_tokens


def insert_capture(
    conn: sqlite3.Connection,
    *,
    session: Session,
    captured_at: int,
    source_kind: str,
    input_tokens: int,
    text_hash: str,
    text_excerpt: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO captures
        (session_id, captured_at, app_name, window_title, source_url, source_host,
         category, source_kind, input_tokens, text_hash, text_excerpt, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.db_id,
            captured_at,
            session.app,
            session.title,
            session.source_url,
            session.source_host,
            session.category,
            source_kind,
            input_tokens,
            text_hash,
            text_excerpt,
            status,
        ),
    )


def save_session(conn: sqlite3.Connection, session: Session, ended_at: int | None) -> None:
    if session.db_id is None:
        cur = conn.execute(
            """
            INSERT INTO sessions
            (started_at, ended_at, app_name, window_title, category, keystrokes,
             duration_seconds, source_url, source_host, input_tokens, output_tokens,
             text_input_tokens, rate_input_tokens, capture_count, last_capture_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.started_at,
                ended_at,
                session.app,
                session.title,
                session.category,
                session.keystrokes,
                session.duration,
                session.source_url,
                session.source_host,
                session.input_tokens,
                session.output_tokens,
                session.text_input_tokens,
                session.rate_input_tokens,
                session.capture_count,
                session.last_capture_status,
            ),
        )
        session.db_id = int(cur.lastrowid)
    else:
        conn.execute(
            """
            UPDATE sessions
            SET ended_at = ?, window_title = ?, category = ?, keystrokes = ?,
                duration_seconds = ?, source_url = ?, source_host = ?,
                input_tokens = ?, output_tokens = ?, text_input_tokens = ?,
                rate_input_tokens = ?, capture_count = ?, last_capture_status = ?
            WHERE id = ?
            """,
            (
                ended_at,
                session.title,
                session.category,
                session.keystrokes,
                session.duration,
                session.source_url,
                session.source_host,
                session.input_tokens,
                session.output_tokens,
                session.text_input_tokens,
                session.rate_input_tokens,
                session.capture_count,
                session.last_capture_status,
                session.db_id,
            ),
        )
    conn.commit()


def session_key(app: str, category: str, source_host: str, window_title: str) -> tuple[str, str, str, str]:
    title_key = re.sub(r"\s+", " ", window_title).strip() if app in BROWSER_APPS else ""
    return app, category, source_host, title_key


def write_report_if_available(day: str) -> None:
    try:
        from report import write_daily_report, write_weekly_report

        path = write_daily_report(day=day, db_path=DB_PATH, out_dir=REPORT_DIR)
        print(f"  report written: {path}")

        if dt.datetime.strptime(day, "%Y-%m-%d").weekday() == 6:  # Sunday: week just ended
            week_path = write_weekly_report(day=day, db_path=DB_PATH, out_dir=REPORT_DIR)
            print(f"  weekly report written: {week_path}")
    except Exception as exc:
        print(f"  report skipped for {day}: {type(exc).__name__}: {exc}")


def print_startup() -> None:
    print()
    print("-- Human Token Tracker --------------------------------")
    print(f"  database      : {DB_PATH}")
    print(f"  reports       : {REPORT_DIR}")
    print("  app name      : ok")
    print(
        "  window title  : "
        + ("ok" if SCREEN_RECORDING else "limited; grant Screen Recording for better titles")
    )
    print(
        "  keystrokes    : "
        + ("ok" if KEYSTROKE_TRACKING else "limited; grant Accessibility for output tokens")
    )
    print("  browser text  : via browser Automation permission when available")
    print("  stop          : Ctrl+C")
    print()


def main() -> None:
    print_startup()

    conn = sqlite3.connect(str(DB_PATH))
    ensure_schema(conn)

    current: Session | None = None
    last_day = local_day(int(time.time()))
    last_browser_ctx = BrowserContext()
    last_flush_at = 0
    last_video_playing: bool | None = None
    last_video_check_at = 0

    def shutdown(_sig, _frame) -> None:
        if current:
            save_session(conn, current, int(time.time()))
        write_report_if_available(local_day(int(time.time())))
        conn.close()
        print("\nStopped. Session saved.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        now = int(time.time())
        current_day = local_day(now)
        if current_day != last_day:
            if current:
                save_session(conn, current, start_of_day_ts(current_day))
                current = None
            write_report_if_available(previous_day(current_day))
            last_day = current_day

        app, window_title = get_active_window()
        ks = pop_keystrokes()

        if app in {"loginwindow", "ScreenSaverEngine", "unknown"}:
            if current:
                save_session(conn, current, now)
                current = None
            time.sleep(POLL_INTERVAL)
            continue

        browser_ctx = last_browser_ctx
        ran_browser_capture = False
        app_changed = current is None or app != current.app
        browser_title_changed = (
            app in BROWSER_APPS
            and current is not None
            and window_title
            and window_title != current.title
        )
        browser_capture_due = now - last_browser_ctx.captured_at >= CAPTURE_INTERVAL
        should_run_browser_capture = app in BROWSER_APPS and (
            browser_capture_due or app_changed or browser_title_changed
        )
        if should_run_browser_capture:
            browser_ctx = capture_browser_context(app, include_text=True)
            last_browser_ctx = browser_ctx
            ran_browser_capture = True

        source_url = browser_ctx.url if app in BROWSER_APPS else ""
        source_host = host_from_url(source_url)
        title = browser_ctx.title or window_title
        category = classify(app, title, source_url)
        if category == "other" and ks > 5:
            category = "creating"

        if category == "video" and app in BROWSER_APPS:
            if app_changed or now - last_video_check_at >= CAPTURE_INTERVAL:
                last_video_playing = check_video_playing(app)
                last_video_check_at = now
        else:
            last_video_playing = None

        confirmed_playing_video = category == "video" and last_video_playing is True
        if system_idle_seconds() >= IDLE_THRESHOLD and not confirmed_playing_video:
            category = "idle"

        if current is None or session_key(
            current.app, current.category, current.source_host, current.title
        ) != session_key(
            app, category, source_host, title
        ):
            if current:
                save_session(conn, current, now)
            current = Session(
                app=app,
                title=title,
                category=category,
                source_url=source_url,
                source_host=source_host,
                started_at=now,
            )
            save_session(conn, current, None)
            label = f"{app} [{category}]"
            if source_host:
                label += f" {source_host}"
            if app in BROWSER_APPS and title:
                label += f" — {title[:80]}"
            print(f"  {time.strftime('%H:%M:%S')}  {label}")

        current.title = title
        current.source_url = source_url
        current.source_host = source_host
        current.category = category
        current.keystrokes += ks
        current.duration += POLL_INTERVAL
        current.output_float = current.keystrokes * 0.25
        current.output_tokens = int(round(current.output_float))

        if ks == 0:
            current.idle_secs += POLL_INTERVAL
        else:
            current.idle_secs = 0

        if (
            app in BROWSER_APPS
            and ran_browser_capture
            and should_capture_text(current.category, current.source_host)
        ):
            if current.db_id is None:
                save_session(conn, current, None)
            gained = add_seen_text_tokens(
                conn,
                session=current,
                captured_at=now,
                day=current_day,
                text=browser_ctx.text,
                status=browser_ctx.status,
            )
            current.capture_count += 1
            current.last_capture_at = now
            current.last_capture_status = browser_ctx.status
            if browser_ctx.status == "text-ok":
                current.last_text_capture_ok_at = now
            if gained > 0:
                current.text_input_tokens += gained

        if should_apply_time_input(current, now):
            _output_rate, input_rate = TOKEN_RATES.get(current.category, (0, 0))
            current.rate_input_float += input_rate * (POLL_INTERVAL / 60)
            current.rate_input_tokens = int(round(current.rate_input_float))

        current.input_tokens = current.text_input_tokens + current.rate_input_tokens

        if now - last_flush_at >= FLUSH_INTERVAL:
            save_session(conn, current, None)
            last_flush_at = now

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
