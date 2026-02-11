#!/usr/bin/env bash
set -euo pipefail

SCRIPT_VERSION="2026-02-11-self-heal-v7"

# Usage:
#   CF_ACCOUNT_ID=... CF_API_TOKEN=... CF_KV_NAMESPACE_ID=... ./cloudflared-kv-updater.sh
# Optional:
#   TUNNEL_HOST_FILTER=trycloudflare.com,cfargotunnel.com
#   KV_KEY=active_url
#   SKIP_KV_UPDATE=true            # run tunnel only without KV write
#   TUNNEL_START_MAX_RETRIES=5
#   RATE_LIMIT_COOLDOWN_SECONDS=300

LOG_DIR=${LOG_DIR:-/var/log/work-time}
mkdir -p "$LOG_DIR"
CLOUDFLARED_LOG="$LOG_DIR/cloudflared.log"

TUNNEL_HOST_FILTER=${TUNNEL_HOST_FILTER:-trycloudflare.com,cfargotunnel.com}
KV_KEY=${KV_KEY:-active_url}
KV_CLEANUP_KEYS=${KV_CLEANUP_KEYS:-active_url,ACTIVE_URL}
SKIP_KV_UPDATE=${SKIP_KV_UPDATE:-false}
TUNNEL_START_MAX_RETRIES=${TUNNEL_START_MAX_RETRIES:-5}
RATE_LIMIT_COOLDOWN_SECONDS=${RATE_LIMIT_COOLDOWN_SECONDS:-300}

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

allowed_host() {
  local host="$1"
  [[ "$host" == *"trycloudflare.com" || "$host" == *"cfargotunnel.com" ]]
}

kv_endpoint_for_key() {
  local key="$1"
  echo "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${CF_KV_NAMESPACE_ID}/values/${key}"
}

kv_get_key() {
  local key="$1"
  curl -fsS -X GET "$(kv_endpoint_for_key "$key")" -H "Authorization: Bearer ${CF_API_TOKEN}" || true
}

kv_delete_key() {
  local key="$1"
  curl -fsS -X DELETE "$(kv_endpoint_for_key "$key")" -H "Authorization: Bearer ${CF_API_TOKEN}" >/dev/null || true
}

sanitize_existing_kv() {
  [[ "${SKIP_KV_UPDATE,,}" == "true" ]] && return 0
  IFS=',' read -ra keys <<<"$KV_CLEANUP_KEYS"
  for key in "${keys[@]}"; do
    key=$(echo "$key" | xargs)
    [[ -z "$key" ]] && continue
    local current
    current=$(kv_get_key "$key")
    [[ -z "$current" ]] && continue
    local host="${current#https://}"
    host="${host%%/*}"
    if ! allowed_host "$host"; then
      echo "[WARN] Found polluted KV value ($current). Deleting key ${key}." >&2
      kv_delete_key "$key"
    fi
  done
}

extract_url() {
  local pattern='https://[A-Za-z0-9.-]+\.(trycloudflare\.com|cfargotunnel\.com)'
  grep -Eo "$pattern" "$CLOUDFLARED_LOG" | tail -n1 || true
}

start_and_discover_url() {
  local attempt
  local saw_rate_limit=false

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
        saw_rate_limit=true
        echo "[WARN] Quick Tunnel rate-limited (429). backing off before retry..." >&2
        kill "$CF_PID" >/dev/null 2>&1 || true
        wait "$CF_PID" 2>/dev/null || true
        CF_PID=""
        sleep $((attempt * 30))
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

  if [[ "$saw_rate_limit" == "true" ]]; then
    return 79
  fi
  return 1
}

while true; do
  sanitize_existing_kv

  URL=""
  if URL=$(start_and_discover_url); then
    :
  else
    rc=$?
    if [[ "$rc" -eq 79 ]]; then
      echo "[WARN] Quick Tunnel still rate-limited (429). cooling down ${RATE_LIMIT_COOLDOWN_SECONDS}s..." >&2
      sleep "$RATE_LIMIT_COOLDOWN_SECONDS"
      continue
    fi
    echo "[ERROR] Failed to discover tunnel URL from $CLOUDFLARED_LOG" >&2
    echo "[HINT] Check cloudflared log: tail -n 120 $CLOUDFLARED_LOG" >&2
    tail -n 40 "$CLOUDFLARED_LOG" >&2 || true
    sleep 30
    continue
  fi

  echo "[INFO] Active tunnel URL: $URL"

  local_host="${URL#https://}"
  local_host="${local_host%%/*}"
  if ! allowed_host "$local_host"; then
    echo "[ERROR] Refusing to write non-tunnel URL to KV: $URL" >&2
    sleep 30
    continue
  fi

  : >"$CLOUDFLARED_LOG"
  cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate >"$CLOUDFLARED_LOG" 2>&1 &
  CF_PID=$!

  if [[ "${SKIP_KV_UPDATE,,}" == "true" ]]; then
    echo "[WARN] SKIP_KV_UPDATE=true, KV update skipped"
    wait "$CF_PID"
    continue
  fi

  curl -fsS -X PUT \
    "$(kv_endpoint_for_key "$KV_KEY")" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: text/plain" \
    --data "$URL" >/dev/null

  kv_readback=$(kv_get_key "$KV_KEY")
  if [[ "$kv_readback" != "$URL" ]]; then
    echo "[ERROR] KV readback mismatch. expected=$URL actual=$kv_readback" >&2
    kill "$CF_PID" >/dev/null 2>&1 || true
    wait "$CF_PID" 2>/dev/null || true
    CF_PID=""
    sleep 10
    continue
  fi

  echo "[INFO] KV key '${KV_KEY}' updated and verified"
  wait "$CF_PID"
done
