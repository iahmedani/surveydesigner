#!/usr/bin/env bash
# Start/stop local Postgres + Redis for development WITHOUT Docker.
# Use when `docker compose up` isn't available (e.g. SageMaker, CI sandboxes).
# Postgres: $SD_PG_BIN (default /usr/lib/postgresql/16/bin, needs ICU). Redis and fallbacks:
# looked up in $SD_SERVICES_PREFIX/bin (default ~/.local/sd-services, a
# conda env created with: conda create -p ~/.local/sd-services -c conda-forge postgresql=16 redis-server)
# then on PATH. Data lives in $SD_DATA_DIR (default ~/.local/sd-data).
#
#   scripts/dev-services.sh start|stop|status
set -euo pipefail
PREFIX="${SD_SERVICES_PREFIX:-$HOME/.local/sd-services}"
DATA="${SD_DATA_DIR:-$HOME/.local/sd-data}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-postgres}"
PGPASS="${POSTGRES_PASSWORD:-r00t}"
PGDB="${POSTGRES_DB:-app_db}"
REDIS_PORT="${REDIS_PORT:-6379}"
[ -d "$PREFIX/bin" ] && export PATH="$PREFIX/bin:$PATH"
# Postgres MUST be built with ICU (the app uses a `case_insensitive` ICU collation).
# conda-forge builds lack ICU, so prefer the distro build (apt install postgresql-16).
# Search order: $SD_PG_BIN, a home-dir extract of the Ubuntu debs (survives SageMaker
# restarts, which wipe /usr), then the system package.
#   Home-dir install: apt-get download postgresql-16 postgresql-client-16 libicu74 libxml2
#                     && for d in *.deb; do dpkg -x "$d" ~/.local/pg16; done
HOME_PG="$HOME/.local/pg16/usr/lib/postgresql/16/bin"
for candidate in "${SD_PG_BIN:-}" "$HOME_PG" /usr/lib/postgresql/16/bin; do
  if [ -n "$candidate" ] && [ -x "$candidate/pg_ctl" ]; then PG_BIN="$candidate"; break; fi
done
if [ -n "${PG_BIN:-}" ]; then
  export PATH="$PG_BIN:$PATH"
  [ "$PG_BIN" = "$HOME_PG" ] && export LD_LIBRARY_PATH="$HOME/.local/pg16/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

start() {
  mkdir -p "$DATA"
  if [ ! -f "$DATA/pg/PG_VERSION" ]; then
    echo "$PGPASS" > "$DATA/.pwfile"
    initdb -D "$DATA/pg" -U "$PGUSER" --pwfile="$DATA/.pwfile" -A md5 >/dev/null
    rm -f "$DATA/.pwfile"
  fi
  # The home-dir Ubuntu build has JIT enabled but no libLLVM -> disable JIT.
  grep -q "^jit = off" "$DATA/pg/postgresql.conf" || echo "jit = off" >> "$DATA/pg/postgresql.conf"
  if ! pg_ctl -D "$DATA/pg" status >/dev/null 2>&1; then
    pg_ctl -D "$DATA/pg" -l "$DATA/pg.log" -o "-p $PGPORT -k /tmp" -w start >/dev/null
  fi
  PGPASSWORD="$PGPASS" psql -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$PGDB'" | grep -q 1 || \
    PGPASSWORD="$PGPASS" createdb -h 127.0.0.1 -p "$PGPORT" -U "$PGUSER" "$PGDB"
  if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    redis-server --port "$REDIS_PORT" --daemonize yes --dir "$DATA" --logfile "$DATA/redis.log"
    for _ in 1 2 3 4 5 6 7 8 9 10; do redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1 && break; sleep 0.3; done
  fi
  status
}
stop() {
  pg_ctl -D "$DATA/pg" stop -m fast 2>/dev/null || true
  redis-cli -p "$REDIS_PORT" shutdown nosave 2>/dev/null || true
}
status() {
  pg_ctl -D "$DATA/pg" status >/dev/null 2>&1 && echo "postgres: up (:$PGPORT)" || echo "postgres: down"
  redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1 && echo "redis: up (:$REDIS_PORT)" || echo "redis: down"
}
case "${1:-status}" in start) start;; stop) stop;; status) status;; *) echo "usage: $0 start|stop|status"; exit 1;; esac
