export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';

    // 1) very simple rate limit using KV (free-plan friendly)
    const minuteKey = `rl:${ip}:${Math.floor(Date.now() / 60000)}`;
    const count = Number((await env.TUNNEL_KV.get(minuteKey)) || '0') + 1;
    await env.TUNNEL_KV.put(minuteKey, String(count), { expirationTtl: 90 });
    if (count > 120) {
      return new Response('Too Many Requests', { status: 429 });
    }

    const kvCandidates = [
      { key: 'active_url', value: await env.TUNNEL_KV.get('active_url') },
      { key: 'ACTIVE_URL', value: await env.TUNNEL_KV.get('ACTIVE_URL') },
    ];

    const allowedHosts = (env.ALLOWED_TUNNEL_HOSTS || 'trycloudflare.com,cfargotunnel.com')
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);

    const deniedHosts = (env.DENIED_TUNNEL_HOSTS || 'api.trycloudflare.com')
      .split(',')
      .map((v) => v.trim().toLowerCase())
      .filter(Boolean);

    let active = null;
    let activeSourceKey = null;
    let invalidHost = null;

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
          break;
        }
        invalidHost = host;
      } catch {
        invalidHost = 'invalid_url';
      }
    }

    // Optional debug endpoint (do not expose secrets)
    if (url.pathname === '/_edge/status') {
      let host = null;
      try {
        host = active ? new URL(active).hostname : null;
      } catch {
        host = 'invalid';
      }
      return Response.json(
        {
          has_active_url: Boolean(active),
          active_url_host: host || invalidHost,
          active_url_source_key: activeSourceKey,
          kv_key_checked: ['active_url', 'ACTIVE_URL'],
          block_direct_api: (env.BLOCK_DIRECT_API || 'false').toLowerCase() === 'true',
          proxy_mode: (env.PROXY_TO_TUNNEL || 'true').toLowerCase() === 'true',
        },
        { status: 200 },
      );
    }

    // 2) optionally block direct /api path at Worker layer
    const blockApi = (env.BLOCK_DIRECT_API || 'false').toLowerCase() === 'true';
    if (blockApi && url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/health')) {
      return new Response('API access denied by edge policy', { status: 403 });
    }

    if (!active) {
      return htmlError('Tunnel endpoint is not ready. KV active_url is empty or invalid.', 503);
    }

    let target;
    try {
      target = new URL(active);
    } catch {
      return htmlError('KV active_url is invalid.', 500);
    }

    if (!allowedHosts.some((host) => target.hostname.endsWith(host)) || deniedHosts.includes(target.hostname.toLowerCase())) {
      return htmlError(`KV active_url host is invalid/non-tunnel. host=${target.hostname}`, 503);
    }

    const upstreamUrl = new URL(`${url.pathname}${url.search}`, target.origin);
    const proxyMode = (env.PROXY_TO_TUNNEL || 'true').toLowerCase() === 'true';

    if (!proxyMode) {
      return Response.redirect(upstreamUrl.toString(), request.method === 'GET' ? 302 : 307);
    }

    const upstreamRequest = new Request(upstreamUrl.toString(), request);
    upstreamRequest.headers.set('x-forwarded-host', url.host);
    upstreamRequest.headers.set('x-servcom-edge', 'cloudflare-worker-proxy');

    return fetch(upstreamRequest, { redirect: 'manual' });
  },
};

function htmlError(message, status) {
  return new Response(
    `<!doctype html><html><body><h2>Work Time Gateway</h2><p>${message}</p></body></html>`,
    {
      status,
      headers: { 'content-type': 'text/html; charset=utf-8' },
    },
  );
}
