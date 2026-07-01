# Token Usage Dashboard

A local web dashboard for tracking Claude Code and Codex token usage by parsing log files from the local filesystem.

![Dashboard Screenshot](assets/screenshot.png)

## Quick Start

```powershell
docker compose up -d --build
```

Open [http://localhost:8765](http://localhost:8765)

## Features

- **Multi-source aggregation** — parses Claude Code JSONL logs, Codex session JSONL logs, and Codex SQLite database
- **Project-level breakdown** — automatically groups Claude Code usage by project folder name
- **Date range filtering** — filter by start/end date
- **Tool filtering** — view Claude Code, Codex, or combined
- **Paginated table** — configurable page size with Previous/Next navigation
- **CSV export** — download filtered data via `/api/export.csv`
- **In-memory caching** — 60-second cache avoids re-parsing log files on repeated requests

## Architecture

```
├── app/
│   ├── server.py          HTTP server, API endpoints
│   └── usage.py           Log parsing, aggregation, caching
├── static/
│   ├── index.html         Single-page application
│   ├── app.js             Frontend logic (ES6+)
│   ├── styles.css         Design system & responsive layout
│   └── favicon.svg        SVG favicon
├── assets/
│   └── screenshot.png     Dashboard screenshot
├── Dockerfile             Python 3.12-slim image
├── docker-compose.yml     Service definition with volume mounts
├── .gitignore
└── README.md
```

### Backend (stdlib only, zero dependencies)

- `ThreadingHTTPServer` serves static files and three API endpoints:
  - `GET /api/usage?tool=&from=&to=&project=` — aggregated usage as JSON
  - `GET /api/export.csv` — same data as CSV download
  - `GET /api/projects` — available Claude Code project directories
- Three generator functions parse log sources into `UsageEvent` dataclasses:
  - **Claude JSONL** — reads `~/.claude/projects/**/*.jsonl`, deduplicates by message ID, resolves project name from `cwd` field
  - **Codex sessions** — reads `~/.codex/sessions/*.jsonl`, deduplicates by total-token tuple
  - **Codex SQLite** — reads `~/.codex/logs_2.sqlite`, queries `response.completed` events, deduplicates by response ID
- `summarize()` aggregates events across 5 period types (daily, weekly, monthly, yearly, total)
- In-process memory cache (60s TTL) avoids re-parsing log files

### Frontend (vanilla, no build step)

- Filter bar with Tool, From/To dates, and Project dropdown
- 6-metric summary bar (Total Tokens, Requests, Input, Output, Cache Hit, Reasoning)
- Paginated daily usage table sorted by date descending
- Three visual states: loading, data, empty/error
- Responsive layout with breakpoints at 1100px and 720px

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TOKEN_USAGE_HOST` | `0.0.0.0` | Server bind address |
| `TOKEN_USAGE_PORT` | `8765` | Server port |
| `TOKEN_USAGE_TZ` | `Asia/Shanghai` | Timezone for log timestamps |
| `TOKEN_USAGE_CLAUDE_PROJECTS` | `~/.claude/projects` | Claude JSONL log directory |
| `TOKEN_USAGE_CODEX_SESSIONS` | `~/.codex/sessions` | Codex session directory |
| `TOKEN_USAGE_CODEX_DB` | `~/.codex/logs_2.sqlite` | Codex SQLite database |

### Data Sources (read-only Docker mounts)

| Source | Host Path |
|---|---|
| Claude Code logs | `~/.claude/projects/**/*.jsonl` |
| Codex session logs | `~/.codex/sessions/**/*.jsonl` |
| Codex SQLite DB | `~/.codex/logs_2.sqlite` |

### Useful Commands

```powershell
# Start
docker compose up -d --build

# View logs
docker compose logs -f

# Stop
docker compose down

# Dev (no Docker)
python -m app.server
```
