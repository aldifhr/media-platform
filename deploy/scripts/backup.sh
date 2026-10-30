#!/usr/bin/env bash
# media-platform Postgres backup (native, no Docker).
# Usage: DATABASE_URL=... ./backup.sh [output_dir]
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL (or source .env first)}"
OUT="${1:-/var/backups/media-platform}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

pg_dump "$DATABASE_URL" | gzip > "$OUT/media_platform_${STAMP}.sql.gz"

# retention: keep 14 days
find "$OUT" -name 'media_platform_*.sql.gz' -mtime +14 -delete

echo "backup: $OUT/media_platform_${STAMP}.sql.gz"
