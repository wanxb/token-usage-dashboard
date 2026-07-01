# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local web dashboard tracking Claude Code / Codex token usage by parsing log files. Python stdlib-only backend + vanilla HTML/CSS/JS frontend. Deployed via Docker Compose.

## Commands

```powershell
# Start (Docker)
docker compose up -d --build

# Stop
docker compose down

# Logs
docker compose logs -f

# Dev (no Docker)
python -m app.server
```

Access: http://localhost:8765

## Architecture

### Backend (`app/`)

- **`server.py`** — `ThreadingHTTPServer` + `SimpleHTTPRequestHandler`. Endpoints: `GET /api/usage` (JSON), `GET /api/export.csv` (CSV), `GET /api/projects` (project list). Query params: `tool` (all/claude/codex), `from`/`to` (ISO date), `project` (name or all).

- **`usage.py`** — Three generator functions parse log sources into `UsageEvent` frozen dataclasses:
  - `iter_claude_events()` — Claude JSONL; deduplicates by message ID; resolves human-readable project name from `cwd` field
  - `iter_codex_session_events()` — Codex session JSONL; deduplicates by total-token tuple
  - `iter_codex_sqlite_events()` — Codex SQLite DB; deduplicates by response ID
  - `summarize()` aggregates into 5 period types (daily/weekly/monthly/yearly/total)
  - `load_summary()` orchestrates: load → filter by date/project → summarize → totals + metadata
  - In-process memory cache (`_cache` dict, 60s TTL) avoids re-parsing on every request

### Frontend (`static/`)

- **`index.html`** — Filter bar (Tool, From, To, Project), 6-metric summary bar, paginated daily-usage table, per-page selector
- **`app.js`** — Vanilla ES6+ fetch-and-render, client-side pagination, 3 visual states (loading, data, error), date-descending sort
- **`styles.css`** — Light theme, CSS custom properties, responsive at 1100px/720px, `prefers-reduced-motion`

### Data sources (read-only Docker mounts)

| Source | Host path |
|---|---|
| Claude JSONL | `~/.claude/projects/**/*.jsonl` |
| Codex sessions | `~/.codex/sessions/**/*.jsonl` |
| Codex SQLite | `~/.codex/logs_2.sqlite` |

### Environment variables

`TOKEN_USAGE_HOST` (0.0.0.0), `TOKEN_USAGE_PORT` (8765), `TOKEN_USAGE_TZ` (Asia/Shanghai), `TOKEN_USAGE_CLAUDE_PROJECTS`, `TOKEN_USAGE_CODEX_SESSIONS`, `TOKEN_USAGE_CODEX_DB`.

### Conventions

- **Python:** PEP 604 annotations, type hints, `snake_case`, `frozen=True` dataclasses, generators (`yield`), `pathlib.Path`
- **JS:** `camelCase`, `const`/`let`, ES6+ (fetch, URLSearchParams, Intl.NumberFormat, template literals)
- **CSS:** Custom properties, responsive, `prefers-reduced-motion`
- **Zero external dependencies** — Python stdlib only, no frontend build step or frameworks
- No tests, linters, or CI/CD currently configured
