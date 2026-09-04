export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const backend = "http://nova.hattena.com:25979";

    // Backend / API / OAuth routes
    const proxyPaths = [
      "/api/",
      "/login",
      "/callback",
      "/logout",
    ];

    const shouldProxy =
      proxyPaths.some(path =>
        path.endsWith("/")
          ? url.pathname.startsWith(path)
          : url.pathname === path
      );

    if (shouldProxy) {
      const target = new URL(
        url.pathname + url.search,
        backend
      );

      const headers = new Headers(request.headers);
      headers.set("Host", new URL(backend).host);

      const response = await fetch(
        new Request(target, {
          method: request.method,
          headers,
          body:
            request.method === "GET" || request.method === "HEAD"
              ? undefined
              : request.body,
          redirect: "manual",
        })
      );

      const responseHeaders = new Headers(response.headers);

      // OAuth redirects must stay on the Worker domain
      const location = responseHeaders.get("Location");

      if (location) {
        try {
          const redirectUrl = new URL(location, backend);

          if (
            redirectUrl.hostname === "nova.hattena.com" &&
            redirectUrl.port === "25979"
          ) {
            redirectUrl.protocol = url.protocol;
            redirectUrl.hostname = url.hostname;
            redirectUrl.port = "";
            responseHeaders.set("Location", redirectUrl.toString());
          }
        } catch {}
      }

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      });
    }

    // Dashboard static files
    return env.ASSETS.fetch(request);
  },
};
