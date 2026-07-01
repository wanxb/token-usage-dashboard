from __future__ import annotations

import json
import sqlite3
import sys
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable, TypeVar
from zoneinfo import ZoneInfo

T = TypeVar("T")

_cache: dict[str, tuple[float, T]] = {}
_CACHE_TTL = 60  # seconds


def cached(key: str, build: Callable[[], T], force: bool = False) -> T:
    """Return cached *build* result or re-build if *CACHE_TTL* expired / *force*."""
    if not force and key in _cache:
        ts, data = _cache[key]
        if _time.monotonic() - ts < _CACHE_TTL:
            return data
    data = build()
    _cache[key] = (_time.monotonic(), data)
    return data

KNOWN_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cached_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True)
class UsageEvent:
    tool: str
    timestamp: datetime
    model: str
    project: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


def parse_iso_datetime(value: str, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def date_bounds(from_date: str | None, to_date: str | None, tz: ZoneInfo):
    start = datetime.combine(date.fromisoformat(from_date), time.min, tzinfo=tz) if from_date else None
    end = datetime.combine(date.fromisoformat(to_date), time.max, tzinfo=tz) if to_date else None
    return start, end


def in_range(timestamp: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start and timestamp < start:
        return False
    if end and timestamp > end:
        return False
    return True


def as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def usage_total(values: dict[str, int]) -> int:
    total = values.get("total_tokens", 0)
    if total:
        return total
    return (
        values.get("input_tokens", 0)
        + values.get("output_tokens", 0)
        + values.get("cache_creation_input_tokens", 0)
        + values.get("cache_read_input_tokens", 0)
    )


def resolve_claude_project(path: Path) -> str:
    """Return the outermost project folder name for a Claude JSONL file.

    Scans the file for all unique ``cwd`` entries and picks the one with the
    fewest path components — that is the project root.
    """
    best: str = path.parent.name  # fallback: encoded dir name
    try:
        lines = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return best

    cwds: set[str] = set()
    with lines:
        for line in lines:
            if '"cwd"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cwd = obj.get("cwd")
            if isinstance(cwd, str):
                cwds.add(cwd)

    if cwds:
        # Shortest path = outermost folder
        shortest = min(cwds, key=lambda p: p.count("\\"))
        name = PureWindowsPath(shortest.rstrip("\\")).name
        if name:
            best = name
    return best


def iter_claude_events(projects_dir: Path, tz: ZoneInfo) -> Iterable[UsageEvent]:
    if not projects_dir.exists():
        return

    seen_message_ids: set[str] = set()
    for path in projects_dir.rglob("*.jsonl"):
        project_name = resolve_claude_project(path)
        try:
            lines = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"skip unreadable Claude file: {path} ({exc})", file=sys.stderr)
            continue

        with lines:
            for line_number, line in enumerate(lines, 1):
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                message = entry.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue

                message_id = message.get("id")
                dedupe_key = message_id if isinstance(message_id, str) and message_id else f"{path}:{line_number}"
                if dedupe_key in seen_message_ids:
                    continue
                seen_message_ids.add(dedupe_key)

                timestamp = parse_iso_datetime(str(entry.get("timestamp", "")), tz)
                if timestamp is None:
                    continue

                values = {key: as_int(usage.get(key)) for key in KNOWN_USAGE_KEYS}
                values["total_tokens"] = usage_total(values)
                yield UsageEvent(
                    tool="claude",
                    project=project_name,
                    timestamp=timestamp,
                    model=str(message.get("model") or ""),
                    **values,
                )


def iter_codex_session_events(sessions_dir: Path, tz: ZoneInfo) -> Iterable[UsageEvent]:
    if not sessions_dir.exists():
        return

    for path in sessions_dir.rglob("*.jsonl"):
        seen_totals: set[tuple[int, int, int, int, int]] = set()
        try:
            lines = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"skip unreadable Codex session file: {path} ({exc})", file=sys.stderr)
            continue

        with lines:
            for line in lines:
                line = line.strip()
                if not line or '"token_count"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                payload = entry.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue

                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                last_usage = info.get("last_token_usage")
                total_usage = info.get("total_token_usage")
                if not isinstance(last_usage, dict) or not isinstance(total_usage, dict):
                    continue

                total_key = (
                    as_int(total_usage.get("input_tokens")),
                    as_int(total_usage.get("cached_input_tokens")),
                    as_int(total_usage.get("output_tokens")),
                    as_int(total_usage.get("reasoning_output_tokens")),
                    as_int(total_usage.get("total_tokens")),
                )
                if total_key in seen_totals:
                    continue
                seen_totals.add(total_key)

                timestamp = parse_iso_datetime(str(entry.get("timestamp", "")), tz)
                if timestamp is None:
                    continue

                input_tokens = as_int(last_usage.get("input_tokens"))
                output_tokens = as_int(last_usage.get("output_tokens"))
                cached_tokens = as_int(last_usage.get("cached_input_tokens"))
                reasoning_tokens = as_int(last_usage.get("reasoning_output_tokens"))
                total_tokens = as_int(last_usage.get("total_tokens")) or input_tokens + output_tokens

                yield UsageEvent(
                    tool="codex",
                    timestamp=timestamp,
                    model="",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=total_tokens,
                )


def iter_codex_sqlite_events(db_path: Path, tz: ZoneInfo) -> Iterable[UsageEvent]:
    if not db_path.exists():
        return

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"skip unreadable Codex db: {db_path} ({exc})", file=sys.stderr)
        return

    seen_response_ids: set[str] = set()
    query = """
        select id, ts, feedback_log_body
        from logs
        where feedback_log_body like '%response.completed%'
        order by ts
    """
    try:
        for row_id, ts_seconds, body in con.execute(query):
            if not isinstance(body, str) or not body.startswith("Received message "):
                continue
            try:
                event = json.loads(body[len("Received message ") :])
            except json.JSONDecodeError:
                continue
            if event.get("type") != "response.completed":
                continue
            response = event.get("response")
            if not isinstance(response, dict):
                continue
            usage = response.get("usage")
            if not isinstance(usage, dict):
                continue

            response_id = response.get("id")
            dedupe_key = response_id if isinstance(response_id, str) and response_id else f"row:{row_id}"
            if dedupe_key in seen_response_ids:
                continue
            seen_response_ids.add(dedupe_key)

            created_at = as_int(response.get("created_at")) or as_int(ts_seconds)
            if not created_at:
                continue
            timestamp = datetime.fromtimestamp(created_at, timezone.utc).astimezone(tz)

            input_details = usage.get("input_tokens_details")
            if not isinstance(input_details, dict):
                input_details = {}
            output_details = usage.get("output_tokens_details")
            if not isinstance(output_details, dict):
                output_details = {}

            input_tokens = as_int(usage.get("input_tokens"))
            output_tokens = as_int(usage.get("output_tokens"))
            total_tokens = as_int(usage.get("total_tokens")) or input_tokens + output_tokens

            yield UsageEvent(
                tool="codex",
                timestamp=timestamp,
                model=str(response.get("model") or ""),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=as_int(input_details.get("cached_tokens")),
                reasoning_tokens=as_int(output_details.get("reasoning_tokens")),
                total_tokens=total_tokens,
            )
    finally:
        con.close()


def period_key(timestamp: datetime, period: str) -> str:
    local_date = timestamp.date()
    if period == "daily":
        return local_date.isoformat()
    if period == "weekly":
        iso_year, iso_week, _ = local_date.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "monthly":
        return f"{local_date.year:04d}-{local_date.month:02d}"
    if period == "yearly":
        return f"{local_date.year:04d}"
    if period == "total":
        return "total"
    raise ValueError(period)


def summarize(events: Iterable[UsageEvent]) -> list[dict[str, object]]:
    periods = ("daily", "weekly", "monthly", "yearly", "total")
    buckets: dict[tuple[str, str, str, str], dict[str, object]] = {}

    for event in events:
        for period in periods:
            key = (event.tool, event.project, period, period_key(event.timestamp, period))
            if key not in buckets:
                buckets[key] = {
                    "tool": event.tool,
                    "project": event.project,
                    "period_type": period,
                    "period": key[3],
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cached_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                }
            bucket = buckets[key]
            bucket["requests"] = int(bucket["requests"]) + 1
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "total_tokens",
            ):
                bucket[field] = int(bucket[field]) + int(getattr(event, field))

    order = {"daily": 0, "weekly": 1, "monthly": 2, "yearly": 3, "total": 4}
    return sorted(
        buckets.values(),
        key=lambda row: (
            str(row["tool"]),
            str(row.get("project", "")),
            order.get(str(row["period_type"]), 9),
            str(row["period"]),
        ),
    )


def load_summary(
    *,
    tool: str,
    from_date: str | None,
    to_date: str | None,
    tz_name: str,
    claude_projects: Path,
    codex_sessions: Path,
    codex_db: Path,
    codex_source: str,
    project: str = "all",
    force_refresh: bool = False,
) -> dict[str, object]:
    tz = ZoneInfo(tz_name)
    start, end = date_bounds(from_date, to_date, tz)
    events: list[UsageEvent] = []
    all_claude_events: list[UsageEvent] = []

    if tool in {"all", "claude"}:
        claude_key = f"claude:{claude_projects}:{tz_name}"
        claude_events = cached(claude_key, lambda: list(iter_claude_events(claude_projects, tz)), force=force_refresh)
        for event in claude_events:
            all_claude_events.append(event)
            if project != "all" and event.project != project:
                continue
            events.append(event)

    if tool in {"all", "codex"}:
        codex_events: list[UsageEvent] = []
        codex_session_key = f"codex_sessions:{codex_sessions}:{tz_name}"
        codex_sqlite_key = f"codex_sqlite:{codex_db}:{tz_name}"
        if codex_source in {"auto", "sessions", "both"}:
            codex_events.extend(
                cached(codex_session_key, lambda: list(iter_codex_session_events(codex_sessions, tz)), force=force_refresh)
            )
        if codex_source == "sqlite" or (codex_source == "auto" and not codex_events):
            codex_events.extend(
                cached(codex_sqlite_key, lambda: list(iter_codex_sqlite_events(codex_db, tz)), force=force_refresh)
            )
        elif codex_source == "both":
            codex_events.extend(
                cached(codex_sqlite_key, lambda: list(iter_codex_sqlite_events(codex_db, tz)), force=force_refresh)
            )
        events.extend(codex_events)

    filtered = [event for event in events if in_range(event.timestamp, start, end)]
    rows = summarize(filtered)
    totals = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    for row in rows:
        if row["period_type"] == "total":
            for key in totals:
                totals[key] += int(row[key])

    all_projects = sorted({
        event.project for event in all_claude_events
    }) if tool in {"all", "claude"} else []

    return {
        "rows": rows,
        "totals": totals,
        "meta": {
            "tool": tool,
            "project": project,
            "from": from_date,
            "to": to_date,
            "timezone": tz_name,
            "codex_source": codex_source,
            "event_count": len(filtered),
            "generated_at": datetime.now(tz).isoformat(timespec="seconds"),
            "sources": {
                "claude_projects": str(claude_projects),
                "codex_sessions": str(codex_sessions),
                "codex_db": str(codex_db),
            },
            "projects": all_projects,
        },
    }
