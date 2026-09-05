export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const backend = (env.BACKEND_URL || "http://nova.hatenna.com:25979").replace(/\/$/, "");

    if (url.pathname === "/healthz") {
      try {
        const r = await fetch(`${backend}/healthz`);
        return new Response(JSON.stringify({
          worker: "ok",
          backend_status: r.status,
          backend_ok: r.ok
        }), {
          status: r.ok ? 200 : 502,
          headers: { "content-type": "application/json" }
        });
      } catch (error) {
        return new Response(JSON.stringify({
          worker: "ok",
          backend: "unreachable",
          error: String(error)
        }), {
          status: 502,
          headers: { "content-type": "application/json" }
        });
      }
    }

    const isBackendRoute =
      url.pathname === "/login" ||
      url.pathname === "/callback" ||
      url.pathname === "/logout" ||
      url.pathname.startsWith("/api/");

    if (!isBackendRoute) return env.ASSETS.fetch(request);

    const target = new URL(url.pathname + url.search, backend);
    const headers = new Headers(request.headers);
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", "https");

    const response = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD"
        ? undefined
        : request.body,
      redirect: "manual"
    });

    const responseHeaders = new Headers(response.headers);
    const location = response.headers.get("location");

    if (location) {
      try {
        const redirectUrl = new URL(location, backend);
        const backendUrl = new URL(backend);

        // Rewrite only redirects that target our backend.
        if (redirectUrl.origin === backendUrl.origin) {
          redirectUrl.protocol = url.protocol;
          redirectUrl.host = url.host;
          responseHeaders.set("location", redirectUrl.toString());
        }
      } catch {}
    }

    // Cloudflare Workers can collapse Set-Cookie when copied through Headers.
    // Re-append every cookie individually so the OAuth session survives.
    responseHeaders.delete("set-cookie");
    const setCookies =
      typeof response.headers.getSetCookie === "function"
        ? response.headers.getSetCookie()
        : [];

    for (const cookie of setCookies) {
      responseHeaders.append("set-cookie", cookie);
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders
    });
  }
};
