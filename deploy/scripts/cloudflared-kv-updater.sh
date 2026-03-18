#!/usr/bin/env bash
set -euo pipefail

SCRIPT_VERSION="2026-03-18-stream-simple-v14-quick-only-lowwrite"

# Usage:
#   CF_ACCOUNT_ID=... CF_API_TOKEN=... CF_KV_NAMESPACE_ID=... ./cloudflared-kv-updater.sh
# Optional:
#   LOCAL_URL=http://127.0.0.1:8080
#   TUNNEL_HOST_FILTER=trycloudflare.com,cfargotunnel.com
#   TUNNEL_HOST_DENY=api.trycloudflare.com
#   KV_KEY=active_url
#   KV_UPDATED_AT_KEY=active_url_updated_at
#   KV_CLEANUP_KEYS=active_url,ACTIVE_URL
#   CLEAR_KV_ON_429=false
#   SKIP_KV_UPDATE=true
#   RATE_LIMIT_COOLDOWN_SECONDS=300
#   NORMAL_RETRY_SECONDS=5

LOG_DIR=${LOG_DIR:-/var/log/work-time}
mkdir -p "$LOG_DIR"
CLOUDFLARED_LOG="$LOG_DIR/cloudflared.log"

LOCAL_URL=${LOCAL_URL:-http://127.0.0.1:8080}
TUNNEL_HOST_FILTER=${TUNNEL_HOST_FILTER:-trycloudflare.com,cfargotunnel.com}
TUNNEL_HOST_DENY=${TUNNEL_HOST_DENY:-api.trycloudflare.com}
KV_KEY=${KV_KEY:-active_url}
KV_UPDATED_AT_KEY=${KV_UPDATED_AT_KEY:-active_url_updated_at}
KV_CLEANUP_KEYS=${KV_CLEANUP_KEYS:-active_url,ACTIVE_URL}
CLEAR_KV_ON_429=${CLEAR_KV_ON_429:-false}
SKIP_KV_UPDATE=${SKIP_KV_UPDATE:-false}
RATE_LIMIT_COOLDOWN_SECONDS=${RATE_LIMIT_COOLDOWN_SECONDS:-300}
NORMAL_RETRY_SECONDS=${NORMAL_RETRY_SECONDS:-5}

echo "[INFO] cloudflared-kv-updater start version=${SCRIPT_VERSION} user=$(id -un)" >&2

missing_vars=()
[[ -n "${CF_ACCOUNT_ID:-}" ]] || missing_vars+=("CF_ACCOUNT_ID")
[[ -n "${CF_API_TOKEN:-}" ]] || missing_vars+=("CF_API_TOKEN")
[[ -n "${CF_KV_NAMESPACE_ID:-}" ]] || missing_vars+=("CF_KV_NAMESPACE_ID")

if [[ "${#missing_vars[@]}" -gt 0 && "${SKIP_KV_UPDATE,,}" != "true" ]]; then
  echo "[ERROR] Missing required env vars: ${missing_vars[*]}" >&2
  exit 78
fi

allowed_host() {
  local host="$1"
  IFS=',' read -ra filters <<<"$TUNNEL_HOST_FILTER"
  for filter in "${filters[@]}"; do
    filter=$(echo "$filter" | xargs)
    [[ -z "$filter" ]] && continue
    [[ "$host" == "$filter" || "$host" == *".${filter}" ]] && return 0
  done
  return 1
}

denied_host() {
  local host="$1"
  IFS=',' read -ra denied <<<"$TUNNEL_HOST_DENY"
  for deny in "${denied[@]}"; do
    deny=$(echo "$deny" | xargs)
    [[ -z "$deny" ]] && continue
    [[ "${host,,}" == "${deny,,}" ]] && return 0
  done
  return 1
}

extract_host() {
  local u="$1"
  local host="${u#https://}"
  host="${host#http://}"
  host="${host%%/*}"
  echo "$host"
}

kv_endpoint_for_key() {
  local key="$1"
  echo "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${CF_KV_NAMESPACE_ID}/values/${key}"
}

kv_get_key() {
  local key="$1"
  curl -fsS -X GET "$(kv_endpoint_for_key "$key")" -H "Authorization: Bearer ${CF_API_TOKEN}" 2>/dev/null || true
}

kv_put_key() {
  local key="$1"
  local value="$2"
  curl -fsS -X PUT \
    "$(kv_endpoint_for_key "$key")" \
    -H "Authorization: Bearer ${CF_API_TOKEN}" \
    -H "Content-Type: text/plain" \
    --data "$value" >/dev/null
}

kv_delete_key() {
  local key="$1"
  local http
  http=$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE "$(kv_endpoint_for_key "$key")" -H "Authorization: Bearer ${CF_API_TOKEN}" || true)
  case "$http" in
    200|204|404)
      ;;
    *)
      echo "[WARN] KV DELETE failed key=${key} http=${http}; fallback empty PUT" >&2
      kv_put_key "$key" ""
      ;;
  esac
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
    local host
    host=$(extract_host "$current")
    if ! allowed_host "$host" || denied_host "$host"; then
      echo "[WARN] Found polluted KV value ($current). Deleting key ${key}." >&2
      kv_delete_key "$key"
    fi
  done
}

clear_active_kv() {
  local reason="$1"
  [[ "${SKIP_KV_UPDATE,,}" == "true" ]] && return 0
  echo "[WARN] Clearing active tunnel KV due to: ${reason}" >&2
  kv_delete_key "$KV_KEY"
  kv_delete_key "$KV_UPDATED_AT_KEY"
}

put_and_verify_kv() {
  local url="$1"
  [[ "${SKIP_KV_UPDATE,,}" == "true" ]] && return 0

  local current
  current=$(kv_get_key "$KV_KEY")
  if [[ "$current" == "$url" ]]; then
    echo "[INFO] KV key '${KV_KEY}' already up-to-date; skip PUT" >&2
    return 0
  fi

  kv_put_key "$KV_KEY" "$url"
  kv_put_key "$KV_UPDATED_AT_KEY" "$(date +%s)"
  local readback
  readback=$(kv_get_key "$KV_KEY")
  if [[ "$readback" != "$url" ]]; then
    echo "[ERROR] KV readback mismatch. expected=$url actual=$readback" >&2
    return 1
  fi
  echo "[INFO] KV key '${KV_KEY}' updated and verified" >&2
  return 0
}

run_quick_tunnel_stream_once() {
  local saw429=0
  local updated=0

  : >"$CLOUDFLARED_LOG"
  echo "[INFO] cloudflared quick stream start local_url=${LOCAL_URL}" >&2

  while IFS= read -r line; do
    echo "$line" | tee -a "$CLOUDFLARED_LOG" >/dev/null

    if [[ "$line" == *'status_code="429 Too Many Requests"'* ]]; then
      saw429=1
    fi

    local url
    url=$(echo "$line" | grep -Eo 'https://[a-z0-9-]+\.[a-z0-9.-]+' | head -n1 || true)
    if [[ -z "$url" ]]; then
      continue
    fi

    local host
    host=$(extract_host "$url")
    if ! allowed_host "$host" || denied_host "$host"; then
      continue
    fi

    echo "[INFO] Discovered tunnel URL: $url" >&2
    if put_and_verify_kv "$url"; then
      updated=1
    fi
  done < <(cloudflared tunnel --url "$LOCAL_URL" --no-autoupdate 2>&1)

  if [[ "$saw429" -eq 1 ]]; then
    return 79
  fi
  if [[ "$updated" -eq 1 ]]; then
    return 0
  fi
  return 1
}

while true; do
  sanitize_existing_kv
  if run_quick_tunnel_stream_once; then
    echo "[WARN] quick tunnel process ended; retry in ${NORMAL_RETRY_SECONDS}s" >&2
    sleep "$NORMAL_RETRY_SECONDS"
    continue
  fi

  rc=$?
  if [[ "$rc" -eq 79 ]]; then
    echo "[WARN] Quick Tunnel rate-limited (429). cooldown ${RATE_LIMIT_COOLDOWN_SECONDS}s" >&2
    if [[ "${CLEAR_KV_ON_429,,}" == "true" ]]; then
      clear_active_kv "quick tunnel rate-limited (429)"
    fi
    sleep "$RATE_LIMIT_COOLDOWN_SECONDS"
  else
    echo "[WARN] quick tunnel ended without valid URL. retry in ${NORMAL_RETRY_SECONDS}s" >&2
    sleep "$NORMAL_RETRY_SECONDS"
  fi
done
