export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const backend =
      env.BACKEND_URL || "http://nova.hattena.com:25979";

    // Health check / diagnostic
    if (url.pathname === "/healthz") {
      try {
        const r = await fetch(`${backend}/healthz`);

        return new Response(
          JSON.stringify({
            worker: "ok",
            backend_status: r.status,
            backend_ok: r.ok
          }),
          {
            status: r.ok ? 200 : 502,
            headers: {
              "content-type": "application/json"
            }
          }
        );
      } catch (e) {
        return new Response(
          JSON.stringify({
            worker: "ok",
            backend: "unreachable",
            error: String(e)
          }),
          {
            status: 502,
            headers: {
              "content-type": "application/json"
            }
          }
        );
      }
    }

    // Backend routes
    const isBackendRoute =
      url.pathname === "/login" ||
      url.pathname === "/callback" ||
      url.pathname === "/logout" ||
      url.pathname.startsWith("/api/");

    // Serve frontend assets
    if (!isBackendRoute) {
      return env.ASSETS.fetch(request);
    }

    // Build backend URL
    const target = new URL(
      url.pathname + url.search,
      backend
    );

    // Forward headers
    const headers = new Headers(request.headers);

    headers.set("X-Forwarded-Host", url.host);
    headers.set(
      "X-Forwarded-Proto",
      url.protocol.replace(":", "")
    );

    // Proxy request to backend
    const response = await fetch(
      new Request(target, {
        method: request.method,
        headers,
        body:
          request.method === "GET" ||
          request.method === "HEAD"
            ? undefined
            : request.body,
        redirect: "manual"
      })
    );

    // Copy response headers
    const responseHeaders = new Headers(
      response.headers
    );

    // Rewrite redirects to Cloudflare domain
    const location = responseHeaders.get("Location");

    if (location) {
      try {
        const redirectUrl = new URL(
          location,
          backend
        );

        redirectUrl.protocol = url.protocol;
        redirectUrl.hostname = url.hostname;
        redirectUrl.port = "";

        responseHeaders.set(
          "Location",
          redirectUrl.toString()
        );
      } catch {}
    }

    // Return backend response
    return new Response(
      response.body,
      {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders
      }
    );
  }
};
