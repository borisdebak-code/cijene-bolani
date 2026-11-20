#!/usr/bin/env bash
# Dnevno osvježavanje u ovom razvojnom okruženju: pokrene lokalni PostgreSQL ako ne radi,
# preuzme nove cjenike i izveze statičnu stranicu u site/ (vidi daily_refresh.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGDATA_DIR=/var/tmp/cijene-pg
PGBIN=/usr/lib/postgresql/16/bin

if ! pg_isready -h /tmp -p 5433 >/dev/null 2>&1; then
  rm -f "$PGDATA_DIR/data/postmaster.pid"
  su postgres -c "$PGBIN/pg_ctl -D $PGDATA_DIR/data -o '-p 5433 -k /tmp' -l $PGDATA_DIR/log start" >/dev/null
  for _ in $(seq 1 20); do pg_isready -h /tmp -p 5433 >/dev/null 2>&1 && break; sleep 1; done
fi

export DATABASE_URL="postgresql+psycopg://postgres@/cijene_real?host=/tmp&port=5433"
export LOG_LEVEL="${LOG_LEVEL:-WARNING}"
cd "$ROOT/backend"
python3 "$ROOT/scripts/daily_refresh.py" --out "$ROOT/site" "$@"
