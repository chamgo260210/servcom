#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   CF_ACCOUNT_ID=... CF_API_TOKEN=... CF_KV_NAMESPACE_ID=... ./cloudflared-kv-updater.sh
# Optional:
#   TUNNEL_HOST_FILTER=trycloudflare.com
#   KV_KEY=active_url

LOG_DIR=${LOG_DIR:-/var/log/work-time}
mkdir -p "$LOG_DIR"
CLOUDFLARED_LOG="$LOG_DIR/cloudflared.log"

TUNNEL_HOST_FILTER=${TUNNEL_HOST_FILTER:-trycloudflare.com}
KV_KEY=${KV_KEY:-active_url}

: "${CF_ACCOUNT_ID:?CF_ACCOUNT_ID is required}"
: "${CF_API_TOKEN:?CF_API_TOKEN is required}"
: "${CF_KV_NAMESPACE_ID:?CF_KV_NAMESPACE_ID is required}"

cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate >"$CLOUDFLARED_LOG" 2>&1 &
CF_PID=$!

cleanup() {
  kill "$CF_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

extract_url() {
  rg -o "https://[a-zA-Z0-9.-]+\.${TUNNEL_HOST_FILTER}" "$CLOUDFLARED_LOG" -N | tail -n1 || true
}

for _ in $(seq 1 60); do
  URL=$(extract_url)
  if [[ -n "${URL}" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "${URL:-}" ]]; then
  echo "[ERROR] Failed to parse trycloudflare URL" >&2
  exit 1
fi

echo "[INFO] Active tunnel URL: $URL"

curl -sS -X PUT \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${CF_KV_NAMESPACE_ID}/values/${KV_KEY}" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: text/plain" \
  --data "$URL" >/dev/null

echo "[INFO] KV key '${KV_KEY}' updated"

wait "$CF_PID"
