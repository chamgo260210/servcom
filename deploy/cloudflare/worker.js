const ACTIVE_CACHE_KEY = '__WORKTIME_ACTIVE_URL_CACHE__';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const cacheTtlSeconds = Number(env.ACTIVE_URL_CACHE_TTL_SECONDS || '30');
    const cacheEnabled = cacheTtlSeconds > 0;

    const state = await resolveActiveState(env, { cacheEnabled, cacheTtlSeconds });

    // Optional debug endpoint (read-only)
    if (url.pathname === '/_edge/status') {
      return Response.json(
        {
          has_active_url: Boolean(state.active),
          active_url_host: state.activeHost || state.invalidHost,
          active_url_source_key: state.activeSourceKey,
          kv_key_checked: ['active_url', 'ACTIVE_URL'],
          active_updated_at_unix: state.activeUpdatedAt || null,
          active_age_seconds: state.activeAgeSeconds,
          max_active_age_seconds: state.maxActiveAgeSeconds,
          cache_enabled: cacheEnabled,
          cache_ttl_seconds: cacheTtlSeconds,
          cache_hit: state.cacheHit,
        },
        { status: 200 },
      );
    }

    if (!state.active) {
      return htmlError('Tunnel endpoint is not ready. KV active_url is empty or invalid.', 503);
    }

    if (
      state.maxActiveAgeSeconds > 0
      && state.activeAgeSeconds !== null
      && state.activeAgeSeconds > state.maxActiveAgeSeconds
    ) {
      return htmlError(
        `Tunnel endpoint is stale. age=${state.activeAgeSeconds}s exceeds max=${state.maxActiveAgeSeconds}s.`,
        503,
      );
    }

    return Response.redirect(
      `${state.target.origin}${url.pathname}${url.search}`,
      request.method === 'GET' ? 302 : 307,
    );
  },
};

async function resolveActiveState(env, { cacheEnabled, cacheTtlSeconds }) {
  if (cacheEnabled) {
    const cached = globalThis[ACTIVE_CACHE_KEY];
    if (cached && Date.now() - cached.cachedAtMs < cacheTtlSeconds * 1000) {
      return { ...cached.state, cacheHit: true };
    }
  }

  const allowedHosts = (env.ALLOWED_TUNNEL_HOSTS || 'trycloudflare.com,cfargotunnel.com')
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);

  const deniedHosts = (env.DENIED_TUNNEL_HOSTS || 'api.trycloudflare.com')
    .split(',')
    .map((v) => v.trim().toLowerCase())
    .filter(Boolean);

  const kvCandidates = [
    { key: 'active_url', value: await env.TUNNEL_KV.get('active_url') },
    { key: 'ACTIVE_URL', value: await env.TUNNEL_KV.get('ACTIVE_URL') },
  ];

  const activeUpdatedAtRaw = await env.TUNNEL_KV.get(env.ACTIVE_URL_UPDATED_AT_KEY || 'active_url_updated_at');
  const activeUpdatedAt = Number(activeUpdatedAtRaw || '0');
  const activeAgeSeconds = activeUpdatedAt > 0
    ? Math.max(0, Math.floor(Date.now() / 1000) - activeUpdatedAt)
    : null;
  const maxActiveAgeSeconds = Number(env.MAX_ACTIVE_URL_AGE_SECONDS || '0');

  let active = null;
  let activeSourceKey = null;
  let invalidHost = null;
  let activeHost = null;
  let target = null;

  for (const candidate of kvCandidates) {
    if (!candidate.value) continue;

    try {
      const parsed = new URL(candidate.value);
      const host = parsed.hostname.toLowerCase();
      if (deniedHosts.includes(host)) {
        invalidHost = host;
        continue;
      }
      if (allowedHosts.some((allowedHost) => host.endsWith(allowedHost))) {
        active = candidate.value;
        activeSourceKey = candidate.key;
        activeHost = host;
        target = parsed;
        break;
      }
      invalidHost = host;
    } catch {
      invalidHost = 'invalid_url';
    }
  }

  if (target && (!allowedHosts.some((host) => target.hostname.endsWith(host)) || deniedHosts.includes(target.hostname.toLowerCase()))) {
    target = null;
    active = null;
  }

  const state = {
    active,
    activeSourceKey,
    invalidHost,
    activeHost,
    target,
    activeUpdatedAt,
    activeAgeSeconds,
    maxActiveAgeSeconds,
    cacheHit: false,
  };

  if (cacheEnabled) {
    globalThis[ACTIVE_CACHE_KEY] = {
      cachedAtMs: Date.now(),
      state,
    };
  }

  return state;
}

function htmlError(message, status) {
  return new Response(
    `<!doctype html><html><body><h2>Work Time Gateway</h2><p>${message}</p></body></html>`,
    {
      status,
      headers: { 'content-type': 'text/html; charset=utf-8' },
    },
  );
}
