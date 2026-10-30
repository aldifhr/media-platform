#!/bin/bash
# Run the ops dashboard with the platform .env under our control.
set -a; . /root/projects/media-platform/.env; set +a
export ADMIN_URL="${ADMIN_URL:-http://127.0.0.1:8002}"
export ADMIN_TOKEN="$MONITOR_AUTH_TOKEN"
export OPS_PASSWORD="${OPS_PASSWORD:-$MONITOR_AUTH_TOKEN}"
export OPS_SESSION_SECRET="${OPS_SESSION_SECRET:-$MONITOR_AUTH_TOKEN}"
export NODE_ENV=production
export PORT=5185
export HOSTNAME=127.0.0.1
cd /root/projects/media-platform/apps/ops
exec /usr/bin/node node_modules/next/dist/bin/next start -p 5185
