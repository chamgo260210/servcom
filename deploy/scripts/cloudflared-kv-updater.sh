#!/usr/bin/env bash
set -euo pipefail

SCRIPT_VERSION="2026-02-11-stream-retry-v4"

# Usage:
#   CF_ACCOUNT_ID=... CF_API_TOKEN=... CF_KV_NAMESPACE_ID=... ./cloudflared-kv-updater.sh
# Optional:
#   TUNNEL_HOST_FILTER=trycloudflare.com,cfargotunnel.com
#   KV_KEY=active_url
#   SKIP_KV_UPDATE=true            # run tunnel only without KV write
#   TUNNEL_START_MAX_RETRIES=5

LOG_DIR=${LOG_DIR:-/var/log/work-time}
mkdir -p "$LOG_DIR"
CLOUDFLARED_LOG="$LOG_DIR/cloudflared.log"

TUNNEL_HOST_FILTER=${TUNNEL_HOST_FILTER:-trycloudflare.com,cfargotunnel.com}
KV_KEY=${KV_KEY:-active_url}
SKIP_KV_UPDATE=${SKIP_KV_UPDATE:-false}
TUNNEL_START_MAX_RETRIES=${TUNNEL_START_MAX_RETRIES:-5}

echo "[INFO] cloudflared-kv-updater start version=${SCRIPT_VERSION} user=$(id -un)" >&2

missing_vars=()
[[ -n "${CF_ACCOUNT_ID:-}" ]] || missing_vars+=("CF_ACCOUNT_ID")
[[ -n "${CF_API_TOKEN:-}" ]] || missing_vars+=("CF_API_TOKEN")
[[ -n "${CF_KV_NAMESPACE_ID:-}" ]] || missing_vars+=("CF_KV_NAMESPACE_ID")

if [[ "${#missing_vars[@]}" -gt 0 && "${SKIP_KV_UPDATE,,}" != "true" ]]; then
  echo "[ERROR] Missing required env vars: ${missing_vars[*]}" >&2
  echo "[HINT] Fill /srv/app/.env with CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID" >&2
  echo "[HINT] If you only want tunnel URL test, set SKIP_KV_UPDATE=true" >&2
  exit 78
fi

CF_PID=""
cleanup() {
  if [[ -n "${CF_PID}" ]]; then
    kill "$CF_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

extract_url() {
  local pattern='https://[A-Za-z0-9.-]+\.(trycloudflare\.com|cfargotunnel\.com)'
  grep -Eo "$pattern" "$CLOUDFLARED_LOG" | tail -n1 || true
}

start_and_discover_url() {
  local attempt
  for attempt in $(seq 1 "$TUNNEL_START_MAX_RETRIES"); do
    : >"$CLOUDFLARED_LOG"
    cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate >"$CLOUDFLARED_LOG" 2>&1 &
    CF_PID=$!
    echo "[INFO] cloudflared attempt=${attempt}/${TUNNEL_START_MAX_RETRIES} pid=${CF_PID}" >&2

    for _ in $(seq 1 90); do
      local url
      url=$(extract_url)
      if [[ -n "${url}" ]]; then
        echo "$url"
        return 0
      fi

      if grep -q 'status_code="429 Too Many Requests"' "$CLOUDFLARED_LOG"; then
        echo "[WARN] Quick Tunnel rate-limited (429). backing off before retry..." >&2
        kill "$CF_PID" >/dev/null 2>&1 || true
        wait "$CF_PID" 2>/dev/null || true
        CF_PID=""
        sleep $((attempt * 20))
        break
      fi

      if ! kill -0 "$CF_PID" >/dev/null 2>&1; then
        echo "[WARN] cloudflared exited before URL discovery. retrying..." >&2
        break
      fi

      sleep 1
    done

    if [[ -n "${CF_PID}" ]]; then
      kill "$CF_PID" >/dev/null 2>&1 || true
      wait "$CF_PID" 2>/dev/null || true
      CF_PID=""
    fi
  done

  return 1
}

URL=$(start_and_discover_url || true)
if [[ -z "${URL:-}" ]]; then
  echo "[ERROR] Failed to discover tunnel URL from $CLOUDFLARED_LOG" >&2
  echo "[HINT] Check cloudflared log: tail -n 120 $CLOUDFLARED_LOG" >&2
  tail -n 40 "$CLOUDFLARED_LOG" >&2 || true
  exit 1
fi

echo "[INFO] Active tunnel URL: $URL"

# Restart cloudflared in long-running mode after successful discovery to avoid stale pid/log states
: >"$CLOUDFLARED_LOG"
cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate >"$CLOUDFLARED_LOG" 2>&1 &
CF_PID=$!

if [[ "${SKIP_KV_UPDATE,,}" == "true" ]]; then
  echo "[WARN] SKIP_KV_UPDATE=true, KV update skipped"
  wait "$CF_PID"
  exit 0
fi

KV_ENDPOINT="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${CF_KV_NAMESPACE_ID}/values/${KV_KEY}"

curl -fsS -X PUT \
  "$KV_ENDPOINT" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: text/plain" \
  --data "$URL" >/dev/null

kv_readback=$(curl -fsS -X GET \
  "$KV_ENDPOINT" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" )

if [[ "$kv_readback" != "$URL" ]]; then
  echo "[ERROR] KV readback mismatch. expected=$URL actual=$kv_readback" >&2
  exit 1
fi

echo "[INFO] KV key '${KV_KEY}' updated and verified"

wait "$CF_PID"
