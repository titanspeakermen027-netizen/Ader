export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const backend = env.BACKEND_URL || "http://nova.hattena.com:25979";

    const isBackendRoute =
      url.pathname === "/login" ||
      url.pathname === "/callback" ||
      url.pathname === "/logout" ||
      url.pathname.startsWith("/api/") ||
      url.pathname === "/healthz";

    if (!isBackendRoute) {
      return env.ASSETS.fetch(request);
    }

    const target = new URL(url.pathname + url.search, backend);

    const headers = new Headers(request.headers);

    // Backend receives the original dashboard cookies.
    // Do not expose backend host to the browser.
    headers.set("X-Forwarded-Host", url.host);
    headers.set("X-Forwarded-Proto", url.protocol.replace(":", ""));

    const response = await fetch(
      new Request(target, {
        method: request.method,
        headers,
        body:
          request.method === "GET" || request.method === "HEAD"
            ? undefined
            : request.body,
        redirect: "manual"
      })
    );

    const responseHeaders = new Headers(response.headers);

    const location = responseHeaders.get("Location");

    if (location) {
      try {
        const redirectUrl = new URL(location, backend);

        if (
          redirectUrl.hostname === new URL(backend).hostname ||
          redirectUrl.hostname === "nova.hattena.com"
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
      headers: responseHeaders
    });
  }
};              ? undefined
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
