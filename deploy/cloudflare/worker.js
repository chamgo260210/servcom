export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const method = request.method;

    // 1) very simple rate limit using KV (free-plan friendly)
    const minuteKey = `rl:${ip}:${Math.floor(Date.now() / 60000)}`;
    const count = Number((await env.TUNNEL_KV.get(minuteKey)) || '0') + 1;
    if (count === 1) {
      await env.TUNNEL_KV.put(minuteKey, String(count), { expirationTtl: 90 });
    } else {
      await env.TUNNEL_KV.put(minuteKey, String(count), { expirationTtl: 90 });
      if (count > 120) {
        return new Response('Too Many Requests', { status: 429 });
      }
    }

    // 2) health endpoint can be proxied directly (optional)
    if (url.pathname === '/health') {
      const active = await env.TUNNEL_KV.get('active_url');
      if (!active) return htmlError('active_url is not configured in KV', 503);
      return Response.redirect(`${active}/health`, 302);
    }

    // 3) optionally block direct /api path at Worker layer
    const blockApi = (env.BLOCK_DIRECT_API || 'false').toLowerCase() === 'true';
    if (blockApi && url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/health')) {
      return new Response('API access denied by edge policy', { status: 403 });
    }

    const active = await env.TUNNEL_KV.get('active_url');
    if (!active) {
      return htmlError('Tunnel endpoint is not ready. Please retry in a moment.', 503);
    }

    let target;
    try {
      target = new URL(active);
    } catch {
      return htmlError('KV active_url is invalid.', 500);
    }

    const allowedHosts = (env.ALLOWED_TUNNEL_HOSTS || 'trycloudflare.com')
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean);

    if (!allowedHosts.some((host) => target.hostname.endsWith(host))) {
      return htmlError('active_url host is not allowed by whitelist.', 403);
    }

    return Response.redirect(`${target.origin}${url.pathname}${url.search}`, method === 'GET' ? 302 : 307);
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
