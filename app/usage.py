from __future__ import annotations

import json
import re
import sqlite3
import sys
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path, PurePath
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


# ── Project name resolution ──────────────────────────────────────────────

_folder_display_cache: dict[str, str] = {}


def _build_project_cache(projects_dir: Path) -> None:
    """Pre-scan every top-level folder under *projects_dir*.

    1) Read a few JSONL files from each folder, collect all cwds, and pick
       the shortest one (fewest backslashes) — that is the folder's
       *root path* (e.g. ``E:\\Projects\\AI_Agent``).
    2) Build a set of all root paths.
    3) For each folder, walk UP from its root path: if the immediate parent
       appears in the set of known roots, this folder is a sub-project of
       that parent.  Assign the parent's display name.
       Otherwise the folder *is* its own canonical project.
    """
    global _folder_display_cache
    if _folder_display_cache:  # already built
        return

    # ---- step 1: folder → root path --------------------------------------
    folder_roots: dict[str, str] = {}   # folder name → full root cwd string
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        folder = entry.name
        all_cwds: set[str] = set()
        # Only scan top-level jsonl files (ignore subdirs like subagents/)
        for child in sorted(p for p in entry.iterdir() if p.is_file() and p.suffix == ".jsonl"):
            if len(all_cwds) >= 8:
                break
            try:
                fh = child.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if '"cwd"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = obj.get("cwd")
                    if isinstance(cwd, str):
                        all_cwds.add(cwd)
                    if len(all_cwds) >= 16:
                        break

        if all_cwds:
            root = PurePath(min(all_cwds, key=lambda p: len(PurePath(p).parts)))
        else:
            root = None

        folder_roots[folder] = root

    # ---- step 2: set of known root cwds ---------------------------------
    all_roots: set[PurePath] = {r for r in folder_roots.values() if r is not None}

    # ---- step 3: canonical project for each folder -----------------------
    for folder, root in folder_roots.items():
        if root is None:
            _folder_display_cache[folder] = _decode_folder_name(folder)
            continue

        # Walk up: is the parent directory a known project root?
        parent = root.parent
        if parent in all_roots:
            # This folder is a sub-project; use parent's display name
            _folder_display_cache[folder] = parent.name
        else:
            _folder_display_cache[folder] = root.name or _decode_folder_name(folder)


def resolve_claude_project(path: Path, projects_dir: Path) -> str:
    """Return the canonical project name for a JSONL file."""
    _build_project_cache(projects_dir)

    try:
        rel = path.relative_to(projects_dir)
        folder = rel.parts[0]
    except ValueError:
        folder = path.parent.name

    return _folder_display_cache.get(folder, _decode_folder_name(folder))


def _decode_folder_name(folder: str) -> str:
    """Decode an encoded folder name to a human-readable project name.

      E--Projects-<name>           → <name>
      C--Users-<user>-Desktop-<n>  → <name>
      C--Users-<user>              → Ad-hoc
    """
    parts = folder.split("--", 1)
    if len(parts) < 2:
        return folder
    rest = parts[1]

    for prefix in ("Projects-",):
        if rest.startswith(prefix):
            return rest[len(prefix) :]

    if rest.startswith("Users-"):
        segments = rest.split("-")
        if "Desktop" in segments:
            idx = segments.index("Desktop")
            return "-".join(segments[idx + 1 :])
        return "Ad-hoc"

    return rest


def iter_claude_events(projects_dir: Path, tz: ZoneInfo) -> Iterable[UsageEvent]:
    if not projects_dir.exists():
        return

    seen_message_ids: set[str] = set()
    for path in projects_dir.rglob("*.jsonl"):
        project_name = resolve_claude_project(path, projects_dir)
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


# ── GitHub Copilot parser ──────────────────────────────────────────

_COPILOT_LOG_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) .*?\] "
    r"\[CopilotClient EventType\(\d+\)\] (.+)$"
)

_COPILOT_EVENTTYPE11_RE = re.compile(
    r"^\[.*?\] \[CopilotClient EventType\(11\)\] (.+)$"
)

_COPILOT_EVENTTYPE9_RE = re.compile(
    r'^\[.*?\] \[CopilotClient EventType\(9\)\] (.+)$'
)

_COPILOT_WORKSPACE_RE = re.compile(
    r"Active workspace path changed to (.+)$"
)


def _extract_copilot_project(workspace_path: str) -> str:
    """Derive a short project name from a full workspace path."""
    # Path: E:\Projects\psp\  →  project: psp
    path = Path(workspace_path.rstrip("\\").rstrip("/"))
    return path.name or "unknown"


def _parse_copilot_timestamp(log_dt_str: str, tz: ZoneInfo) -> datetime | None:
    """Parse a Copilot log timestamp like '2026-05-26 01:59:33.550'."""
    try:
        dt = datetime.strptime(log_dt_str, "%Y-%m-%d %H:%M:%S.%f")
        return dt.replace(tzinfo=tz)
    except ValueError:
        return None


def iter_copilot_events(logs_dir: Path, tz: ZoneInfo) -> Iterable[UsageEvent]:
    """Parse GitHub Copilot chat log files for token usage events.

    Looks for ``*.chat.log`` files in *logs_dir* that contain
    ``EventType(11)`` lines with JSON token data.

    Tracks:
    - workspace path from ``Active workspace path changed to ...`` lines
    - model name from ``EventType(9)`` request JSON
    """
    if not logs_dir.exists():
        return

    logs = sorted(logs_dir.glob("*.chat.log"))
    if not logs:
        # Fallback: check subdirectories
        logs = sorted(logs_dir.rglob("*.chat.log"))

    for path in logs:
        current_project = ""
        last_model = ""
        try:
            lines = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue

        with lines:
            for line in lines:
                line = line.rstrip("\n\r")
                if not line:
                    continue

                # ── Track workspace / project ────────────────────────
                m = _COPILOT_WORKSPACE_RE.search(line)
                if m:
                    current_project = _extract_copilot_project(m.group(1))
                    continue

                # ── Track model from EventType(9) JSON ───────────────
                m = _COPILOT_EVENTTYPE9_RE.match(line)
                if m:
                    try:
                        req = json.loads(m.group(1))
                        model = req.get("model", "")
                        if isinstance(model, str) and model:
                            last_model = model
                    except json.JSONDecodeError:
                        pass
                    continue

                # ── Token usage from EventType(11) JSON ──────────────
                m = _COPILOT_EVENTTYPE11_RE.match(line)
                if not m:
                    continue

                token_data_str = m.group(1)
                try:
                    token_arr = json.loads(token_data_str)
                except json.JSONDecodeError:
                    continue

                if not isinstance(token_arr, list) or not token_arr:
                    continue
                token_data = token_arr[0]
                if not isinstance(token_data, dict):
                    continue

                # Timestamp from the log-line prefix
                log_m = _COPILOT_LOG_RE.match(line)
                if not log_m:
                    continue
                timestamp = _parse_copilot_timestamp(log_m.group(1), tz)
                if timestamp is None:
                    continue

                input_tokens = as_int(token_data.get("InputTokenCount"))
                output_tokens = as_int(token_data.get("OutputTokenCount"))
                total_tokens = as_int(token_data.get("TotalTokenCount"))
                cached_tokens = as_int(token_data.get("CachedInputTokenCount"))

                # Reasoning: check top-level then AdditionalCounts
                reasoning_tokens = as_int(token_data.get("ReasoningTokenCount"))
                if not reasoning_tokens:
                    additional = token_data.get("AdditionalCounts")
                    if isinstance(additional, dict):
                        reasoning_tokens = as_int(additional.get("reasoning_tokens"))

                yield UsageEvent(
                    tool="copilot",
                    timestamp=timestamp,
                    model=last_model,
                    project=current_project,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=total_tokens or (input_tokens + output_tokens),
                )


# ── OpenCode parser ───────────────────────────────────────────────


def _ms_to_dt(ts_ms: int, tz: ZoneInfo) -> datetime:
    """Convert millisecond epoch to timezone-aware datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(tz)


def _parse_opencode_tokens(
    tokens_data: object,
) -> tuple[int, int, int, int]:
    """Extract (input, output, cached, reasoning) from OpenCode token data.

    Handles both ``message.data.tokens`` (flat dict):
        {"total": N, "input": N, "output": N, "reasoning": N,
         "cache": {"read": N, "write": N}}

    And ``part.data.tokens`` (same structure).
    """
    if not isinstance(tokens_data, dict):
        return 0, 0, 0, 0
    input_t = as_int(tokens_data.get("input"))
    output_t = as_int(tokens_data.get("output"))
    reasoning_t = as_int(tokens_data.get("reasoning"))

    cache_data = tokens_data.get("cache")
    if isinstance(cache_data, dict):
        cached_t = as_int(cache_data.get("read"))
    else:
        cached_t = 0

    return input_t, output_t, cached_t, reasoning_t


def _resolve_opencode_project(
    session_id: str, session_cache: dict[str, dict]
) -> str:
    """Resolve a session ID to a human-readable project name."""
    session = session_cache.get(session_id)
    if session is None:
        return session_id[:20]  # fallback: truncated session ID
    return session.get("directory", "") or session.get("title", "") or session_id[:20]


def _build_session_cache(con: sqlite3.Connection) -> dict[str, dict]:
    """Pre-load session table into a dict keyed by session ID."""
    cache: dict[str, dict] = {}
    try:
        cols = [d[0] for d in con.execute("SELECT * FROM session LIMIT 0").description]
        for row in con.execute("SELECT * FROM session"):
            rec = dict(zip(cols, row))
            sid = rec.get("id")
            if sid:
                cache[sid] = rec
    except sqlite3.Error:
        pass
    return cache


def iter_opencode_events(db_path: Path, tz: ZoneInfo) -> Iterable[UsageEvent]:
    """Parse OpenCode SQLite database for per-step and per-message token usage.

    Reads two data sources in order:
    1. ``part`` table — rows with ``type = 'step-finish'`` holding per-step token data
    2. ``message`` table — assistant message rows with top-level ``tokens`` field
    """
    if not db_path.exists():
        return

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return

    try:
        session_cache = _build_session_cache(con)

        # ── Source 1: part table, type = 'step-finish' ────────────
        try:
            cols = [d[0] for d in con.execute("SELECT * FROM part LIMIT 0").description]
            for row in con.execute(
                "SELECT * FROM part WHERE data LIKE '%step-finish%'"
            ):
                rec = dict(zip(cols, row))
                raw = rec.get("data")
                if not isinstance(raw, str):
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict) or parsed.get("type") != "step-finish":
                    continue

                tokens_data = parsed.get("tokens")
                if not isinstance(tokens_data, dict):
                    continue

                input_t, output_t, cached_t, reasoning_t = _parse_opencode_tokens(tokens_data)
                total_t = as_int(tokens_data.get("total"))
                if not total_t and not input_t and not output_t:
                    continue

                # Timestamp from part.time_created (milliseconds epoch)
                ts_ms = rec.get("time_created")
                if not isinstance(ts_ms, (int, float)):
                    continue
                timestamp = _ms_to_dt(int(ts_ms), tz)

                # Model and project from session/message context
                session_id = rec.get("session_id") or ""
                project = _resolve_opencode_project(session_id, session_cache)

                # Try to find model from message.data (upstream)
                model = ""
                msg_id = rec.get("message_id")
                if msg_id:
                    try:
                        cur = con.execute(
                            "SELECT data FROM message WHERE id = ?", (msg_id,)
                        )
                        msg_row = cur.fetchone()
                        if msg_row:
                            msg_data = json.loads(msg_row[0])
                            if isinstance(msg_data, dict):
                                model = str(
                                    msg_data.get("modelID")
                                    or msg_data.get("model", "")
                                    or ""
                                )
                    except (sqlite3.Error, json.JSONDecodeError):
                        pass

                yield UsageEvent(
                    tool="opencode",
                    timestamp=timestamp,
                    model=model,
                    project=project,
                    input_tokens=input_t,
                    output_tokens=output_t,
                    cached_tokens=cached_t,
                    reasoning_tokens=reasoning_t,
                    total_tokens=total_t or (input_t + output_t),
                )
        except sqlite3.Error:
            pass

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
    copilot_logs: Path = Path(""),
    opencode_db: Path = Path(""),
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

    if tool in {"all", "copilot"}:
        copilot_key = f"copilot:{copilot_logs}:{tz_name}"
        copilot_events = cached(copilot_key, lambda: list(iter_copilot_events(copilot_logs, tz)), force=force_refresh)
        if project == "all":
            events.extend(copilot_events)
        else:
            events.extend(e for e in copilot_events if e.project == project)

    if tool in {"all", "opencode"}:
        opencode_key = f"opencode:{opencode_db}:{tz_name}"
        opencode_events = cached(opencode_key, lambda: list(iter_opencode_events(opencode_db, tz)), force=force_refresh)
        if project == "all":
            events.extend(opencode_events)
        else:
            events.extend(e for e in opencode_events if e.project == project)

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
                "copilot_logs": str(copilot_logs),
                "opencode_db": str(opencode_db),
            },
            "projects": all_projects,
        },
    }
