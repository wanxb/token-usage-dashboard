FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOKEN_USAGE_HOST=0.0.0.0 \
    TOKEN_USAGE_PORT=8765 \
    TOKEN_USAGE_CLAUDE_PROJECTS=/host/claude/projects \
    TOKEN_USAGE_CODEX_SESSIONS=/host/codex/sessions \
    TOKEN_USAGE_CODEX_DB=/host/codex/logs_2.sqlite

WORKDIR /app
COPY app ./app
COPY static ./static

EXPOSE 8765
CMD ["python", "-m", "app.server"]
