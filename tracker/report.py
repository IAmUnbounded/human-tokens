#!/usr/bin/env python3
"""Generate a daily Human Token report from the SQLite tracker database."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "sessions.db"
REPORT_DIR = ROOT / "reports"


TOKEN_RATES: dict[str, tuple[int, int]] = {
    "creating": (0, 5),
    "reading": (0, 200),
    "ai": (0, 150),
    "video": (0, 0),
    "social": (0, 120),
    "communication": (0, 75),
    "other": (0, 0),
    "idle": (0, 0),
}

CONSUMING_CATEGORIES = {"reading", "video", "social", "communication", "ai"}
BROWSER_CREATING_OUTPUT_TOKEN_THRESHOLD = int(
    os.environ.get("HUMAN_TOKENS_BROWSER_CREATING_OUTPUT_TOKEN_THRESHOLD", "8")
)
IMAGE_INPUT_TOKENS = float(os.environ.get("HUMAN_TOKENS_IMAGE_INPUT_TOKENS", "1024"))
VIDEO_FPS = float(os.environ.get("HUMAN_TOKENS_VIDEO_FPS", "30"))
WRITING_HOSTS = {"docs.google.com"}
WRITING_TITLE_MARKERS = (
    " - google docs",
    " - google sheets",
    " - google slides",
)


def local_today() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def day_bounds(day: str) -> tuple[int, int]:
    start = dt.datetime.strptime(day, "%Y-%m-%d")
    end = start + dt.timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def week_bounds(day: str) -> tuple[int, int]:
    """Return a [start, end) range covering the 7 days ending on `day` (inclusive)."""
    end_start = dt.datetime.strptime(day, "%Y-%m-%d") + dt.timedelta(days=1)
    start = end_start - dt.timedelta(days=7)
    return int(start.timestamp()), int(end_start.timestamp())


def fmt_tokens(value: int | float) -> str:
    value = int(round(value))
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def fmt_time(seconds: int | float) -> str:
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def time_input_tokens(category: str, duration: int | float) -> int:
    if category == "video":
        return round(max(0, duration) * VIDEO_FPS * IMAGE_INPUT_TOKENS)

    _output_rate, input_rate = TOKEN_RATES.get(category, (0, 0))
    return round(input_rate * (max(0, duration) / 60))


def legacy_tokens(category: str, duration: int, keystrokes: int) -> tuple[int, int]:
    output = round(keystrokes * 0.25)
    input_ = time_input_tokens(category, duration)
    return output, input_


def host_matches(host: str, candidates: set[str]) -> bool:
    return any(host == candidate or host.endswith("." + candidate) for candidate in candidates)


def is_browser_writing_surface(source_host: str, window_title: str) -> bool:
    host = source_host.lower()
    title = window_title.lower()
    return host_matches(host, WRITING_HOSTS) or any(
        marker in title for marker in WRITING_TITLE_MARKERS
    )


def effective_category(
    category: str,
    source_host: str,
    window_title: str,
    output_tokens: int,
) -> str:
    if (
        category == "reading"
        and output_tokens >= BROWSER_CREATING_OUTPUT_TOKEN_THRESHOLD
        and is_browser_writing_surface(source_host, window_title)
    ):
        return "creating"
    return category


def write_daily_report(
    *,
    day: str | None = None,
    db_path: Path = DB_PATH,
    out_dir: Path = REPORT_DIR,
) -> Path:
    day = day or local_today()
    start, end = day_bounds(day)
    out_dir.mkdir(parents=True, exist_ok=True)
    return write_range_report(
        start=start,
        end=end,
        title=f"Human Token Report - {day}",
        out_path=out_dir / f"{day}.md",
        db_path=db_path,
    )


def write_weekly_report(
    *,
    day: str | None = None,
    db_path: Path = DB_PATH,
    out_dir: Path = REPORT_DIR,
) -> Path:
    """Write a rollup report for the 7 days ending on `day` (inclusive, default today)."""
    day = day or local_today()
    start, end = week_bounds(day)
    start_label = local_day(start)
    out_dir.mkdir(parents=True, exist_ok=True)
    return write_range_report(
        start=start,
        end=end,
        title=f"Human Token Weekly Report - {start_label} to {day}",
        out_path=out_dir / f"week-{day}.md",
        db_path=db_path,
    )


def local_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def write_range_report(
    *,
    start: int,
    end: int,
    title: str,
    out_path: Path,
    db_path: Path = DB_PATH,
) -> Path:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    session_has_tokens = has_column(conn, "sessions", "input_tokens") and has_column(
        conn, "sessions", "output_tokens"
    )
    session_has_source = has_column(conn, "sessions", "source_host")
    captures_available = has_table(conn, "captures")

    select_cols = [
        "app_name",
        "window_title",
        "category",
        "keystrokes",
        "duration_seconds",
    ]
    if session_has_tokens:
        select_cols.extend(
            [
                "input_tokens",
                "output_tokens",
                "text_input_tokens",
                "rate_input_tokens",
                "capture_count",
                "last_capture_status",
            ]
        )
    if session_has_source:
        select_cols.extend(["source_host", "source_url"])

    sessions = conn.execute(
        f"""
        SELECT {", ".join(select_cols)}
        FROM sessions
        WHERE started_at >= ? AND started_at < ?
        ORDER BY started_at ASC
        """,
        (start, end),
    ).fetchall()

    totals = {
        "input": 0,
        "output": 0,
        "duration": 0,
        "creating": 0,
        "consuming": 0,
        "keystrokes": 0,
        "text_input": 0,
        "rate_input": 0,
        "captures": 0,
    }
    by_app: dict[str, dict[str, int | str]] = {}
    by_category: dict[str, dict[str, int]] = {}

    for row in sessions:
        duration = int(row["duration_seconds"] or 0)
        keystrokes = int(row["keystrokes"] or 0)
        if session_has_tokens and (
            int(row["input_tokens"] or 0)
            + int(row["output_tokens"] or 0)
            + int(row["text_input_tokens"] or 0)
            + int(row["rate_input_tokens"] or 0)
        ):
            output_tokens = int(row["output_tokens"] or 0)
            input_tokens = int(row["input_tokens"] or 0)
            text_input = int(row["text_input_tokens"] or 0)
            rate_input = int(row["rate_input_tokens"] or 0)
            capture_count = int(row["capture_count"] or 0)
        else:
            output_tokens, input_tokens = legacy_tokens(
                str(row["category"]), duration, keystrokes
            )
            text_input = 0
            rate_input = input_tokens
            capture_count = 0

        host = str(row["source_host"] or "") if session_has_source else ""
        app_key = str(row["app_name"])
        category = effective_category(
            str(row["category"]),
            host,
            str(row["window_title"] or ""),
            output_tokens,
        )
        if category == "video":
            rate_input = time_input_tokens(category, duration)
            input_tokens = text_input + rate_input
        label = f"{app_key} ({host})" if host else app_key
        app_group_key = f"{label}|{category}"

        totals["input"] += input_tokens
        totals["output"] += output_tokens
        totals["duration"] += duration
        totals["keystrokes"] += keystrokes
        totals["text_input"] += text_input
        totals["rate_input"] += rate_input
        totals["captures"] += capture_count
        if category == "creating":
            totals["creating"] += duration
        if category in CONSUMING_CATEGORIES:
            totals["consuming"] += duration

        if app_group_key not in by_app:
            by_app[app_group_key] = {
                "label": label,
                "category": category,
                "duration": 0,
                "input": 0,
                "output": 0,
                "keystrokes": 0,
            }
        by_app[app_group_key]["duration"] = int(by_app[app_group_key]["duration"]) + duration
        by_app[app_group_key]["input"] = int(by_app[app_group_key]["input"]) + input_tokens
        by_app[app_group_key]["output"] = int(by_app[app_group_key]["output"]) + output_tokens
        by_app[app_group_key]["keystrokes"] = int(by_app[app_group_key]["keystrokes"]) + keystrokes

        if category not in by_category:
            by_category[category] = {"duration": 0, "input": 0, "output": 0}
        by_category[category]["duration"] += duration
        by_category[category]["input"] += input_tokens
        by_category[category]["output"] += output_tokens

    recent_captures: list[sqlite3.Row] = []
    if captures_available:
        recent_captures = conn.execute(
            """
            SELECT captured_at, app_name, source_host, input_tokens, text_excerpt, status
            FROM captures
            WHERE captured_at >= ? AND captured_at < ? AND input_tokens > 0
            ORDER BY captured_at DESC
            LIMIT 10
            """,
            (start, end),
        ).fetchall()

    conn.close()

    ratio = (
        f"1:{totals['input'] / totals['output']:.1f}"
        if totals["output"] > 0
        else "input only"
    )

    lines: list[str] = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- Output tokens: {fmt_tokens(totals['output'])}",
        f"- Input tokens: {fmt_tokens(totals['input'])}",
        f"- Output:input ratio: {ratio}",
        f"- Active time: {fmt_time(totals['duration'])}",
        f"- Creating time: {fmt_time(totals['creating'])}",
        f"- Consuming time: {fmt_time(totals['consuming'])}",
        f"- Keystrokes: {int(totals['keystrokes']):,}",
        f"- Visible text input: {fmt_tokens(totals['text_input'])}",
        f"- Time/media input: {fmt_tokens(totals['rate_input'])}",
        "",
        "## Category Mix",
        "",
        "| Category | Time | Input | Output |",
        "| --- | ---: | ---: | ---: |",
    ]

    for category, row in sorted(
        by_category.items(), key=lambda item: item[1]["duration"], reverse=True
    ):
        lines.append(
            f"| {category} | {fmt_time(row['duration'])} | {fmt_tokens(row['input'])} | {fmt_tokens(row['output'])} |"
        )

    lines.extend(
        [
            "",
            "## Top Apps And Sites",
            "",
            "| App or site | Category | Time | Input | Output |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )

    for row in sorted(by_app.values(), key=lambda item: int(item["duration"]), reverse=True)[
        :12
    ]:
        lines.append(
            f"| {row['label']} | {row['category']} | {fmt_time(int(row['duration']))} | "
            f"{fmt_tokens(int(row['input']))} | {fmt_tokens(int(row['output']))} |"
        )

    lines.extend(["", "## Text Captured", ""])
    if recent_captures:
        for row in recent_captures:
            when = dt.datetime.fromtimestamp(int(row["captured_at"])).strftime("%H:%M")
            host = row["source_host"] or row["app_name"]
            excerpt = str(row["text_excerpt"] or "").replace("|", "\\|").strip()
            lines.append(
                f"- {when} - {host} - {fmt_tokens(int(row['input_tokens'] or 0))} tokens - {excerpt}"
            )
    else:
        lines.append("- No visible browser text was captured for this period.")

    lines.extend(
        [
            "",
            "## Capture Health",
            "",
            f"- Browser capture attempts: {int(totals['captures'])}",
            f"- Last generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
    )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Human Token report.")
    parser.add_argument("--day", default=local_today(), help="YYYY-MM-DD, default today")
    parser.add_argument(
        "--period",
        choices=["day", "week"],
        default="day",
        help="'day' for a single-day report, 'week' for the 7 days ending on --day",
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()

    writer = write_weekly_report if args.period == "week" else write_daily_report
    path = writer(day=args.day, db_path=args.db, out_dir=args.out_dir)
    print(path)


if __name__ == "__main__":
    main()
