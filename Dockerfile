# syntax=docker/dockerfile:1.7
# Bot sinyal saham IDX — image production.
# - Dependency dari lockfile (requirements.txt hasil `uv export`), tanpa dev tools.
# - Berjalan sebagai user non-root; .env TIDAK di-bake (gunakan env_file/secret saat runtime).
# - Data persisten di /app/var (mount volume).

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
# numba (dependency pandas-ta) membutuhkan compiler hanya bila wheel tidak tersedia; slim biasanya cukup.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" \
    TZ=Asia/Jakarta VAR_DIR=/app/var CONFIG_DIR=/app/config
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 bot && useradd --system --uid 10001 --gid bot --home /app bot
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# Hanya kode & konfigurasi contoh; .env, var/, .git, test dikecualikan lewat .dockerignore.
COPY --chown=bot:bot pyproject.toml alembic.ini main.py ./
COPY --chown=bot:bot config ./config
COPY --chown=bot:bot core ./core
COPY --chown=bot:bot data ./data
COPY --chown=bot:bot engine ./engine
COPY --chown=bot:bot ai ./ai
COPY --chown=bot:bot bot ./bot
COPY --chown=bot:bot notifications ./notifications
COPY --chown=bot:bot storage ./storage
COPY --chown=bot:bot backtest ./backtest
COPY --chown=bot:bot scripts ./scripts
COPY --chown=bot:bot docs ./docs
RUN mkdir -p /app/var && chown -R bot:bot /app/var
VOLUME ["/app/var"]
USER bot
# Health check tanpa rahasia: heartbeat ditulis scheduler tiap health tick (15 menit) + saat start.
HEALTHCHECK --interval=5m --timeout=10s --start-period=2m --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]
STOPSIGNAL SIGTERM
# Tanpa ENTRYPOINT tetap: `docker run img` = bot; `docker run img python scripts/healthcheck.py` tetap bisa.
CMD ["python", "main.py", "run"]
