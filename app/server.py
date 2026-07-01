from __future__ import annotations

import csv
import io
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .usage import load_summary

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


class Handler(SimpleHTTPRequestHandler):
    server_version = "TokenUsageDashboard/1.0"

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        rel = parsed.path.lstrip("/") or "index.html"
        if rel.startswith("api/"):
            rel = "index.html"
        return str((STATIC_DIR / rel).resolve())

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/usage":
            self.handle_usage(parsed.query)
            return
        if parsed.path == "/api/export.csv":
            self.handle_csv(parsed.query)
            return
        if parsed.path == "/api/projects":
            self.handle_projects()
            return
        super().do_GET()

    def handle_usage(self, query: str) -> None:
        try:
            payload = self.summary_from_query(query)
            self.send_json(200, payload)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def handle_csv(self, query: str) -> None:
        try:
            payload = self.summary_from_query(query)
            rows = payload["rows"]
            buffer = io.StringIO()
            if rows:
                writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            data = buffer.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=token-usage.csv")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def handle_projects(self) -> None:
        try:
            claude_dir = env_path("TOKEN_USAGE_CLAUDE_PROJECTS", str(Path.home() / ".claude" / "projects"))
            projects = sorted(d.name for d in claude_dir.iterdir() if d.is_dir()) if claude_dir.exists() else []
            self.send_json(200, {"projects": projects})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def summary_from_query(self, query: str):
        params = parse_qs(query)
        tool = first(params, "tool", "all")
        project = first(params, "project", "all")
        force_refresh = first(params, "refresh", "0") == "1"
        if tool not in {"all", "claude", "codex"}:
            raise ValueError("invalid tool")
        return load_summary(
            tool=tool,
            project=project,
            force_refresh=force_refresh,
            from_date=first(params, "from", None),
            to_date=first(params, "to", None),
            tz_name=os.environ.get("TOKEN_USAGE_TZ", "Asia/Shanghai"),
            claude_projects=env_path("TOKEN_USAGE_CLAUDE_PROJECTS", str(Path.home() / ".claude" / "projects")),
            codex_sessions=env_path("TOKEN_USAGE_CODEX_SESSIONS", str(Path.home() / ".codex" / "sessions")),
            codex_db=env_path("TOKEN_USAGE_CODEX_DB", str(Path.home() / ".codex" / "logs_2.sqlite")),
            codex_source="auto",
        )

    def send_json(self, status: int, payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def first(params: dict[str, list[str]], key: str, default: str | None) -> str | None:
    values = params.get(key)
    if not values:
        return default
    value = values[0].strip()
    return value if value else default


def main() -> int:
    host = os.environ.get("TOKEN_USAGE_HOST", "0.0.0.0")
    port = int(os.environ.get("TOKEN_USAGE_PORT", "8765"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Token Usage Dashboard listening on http://{host}:{port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
