/**
 * Ader Cloudflare Worker
 *
 * Serves the static frontend from Workers Static Assets and proxies only
 * dashboard/OAuth routes to the existing FastAPI backend.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const backend = (env.BACKEND_URL || "http://nova.hatenna.com:25979").replace(/\/$/, "");

    if (url.pathname === "/healthz") {
      return proxyBackend(request, env, backend, url, {
        publicHealth: true
      });
    }

    if (url.pathname === "/.well-known/discord") {
      return new Response(
        "dh=533e699e4950bb351c93676ca7d311ca5e329a06",
        {
          status: 200,
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store"
          }
        }
      );
    }

    const isBackendRoute =
      url.pathname === "/login" ||
      url.pathname === "/callback" ||
      url.pathname === "/logout" ||
      url.pathname.startsWith("/api/");

    if (!isBackendRoute) {
      return serveAsset(request, env);
    }

    return proxyBackend(request, env, backend, url);
  }
};

async function serveAsset(request, env) {
  if (!env.ASSETS || typeof env.ASSETS.fetch !== "function") {
    return new Response(
      "Ader Worker is missing the ASSETS binding. Deploy with wrangler.jsonc/static assets configured.",
      {
        status: 500,
        headers: {
          "content-type": "text/plain; charset=utf-8",
          "cache-control": "no-store"
        }
      }
    );
  }

  return env.ASSETS.fetch(request);
}

async function proxyBackend(request, env, backend, publicUrl, options = {}) {
  const target = new URL(publicUrl.pathname + publicUrl.search, backend);
  const headers = new Headers(request.headers);

  // The backend uses these to build OAuth redirect URIs from the public
  // Worker origin instead of the private/backend origin.
  headers.set("X-Forwarded-Host", publicUrl.host);
  headers.set("X-Forwarded-Proto", publicUrl.protocol.replace(":", ""));

  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD"
        ? undefined
        : request.body,
      redirect: "manual",
      cache: "no-store"
    });

    if (options.publicHealth) {
      return new Response(
        JSON.stringify({
          worker: "ok",
          backend_status: response.status,
          backend_ok: response.ok
        }),
        {
          status: response.ok ? 200 : 502,
          headers: {
            "content-type": "application/json",
            "cache-control": "no-store"
          }
        }
      );
    }

    return buildProxiedResponse(response, backend, publicUrl);
  } catch (error) {
    if (options.publicHealth) {
      return new Response(
        JSON.stringify({
          worker: "ok",
          backend: "unreachable",
          error: String(error)
        }),
        {
          status: 502,
          headers: {
            "content-type": "application/json",
            "cache-control": "no-store"
          }
        }
      );
    }

    return new Response(
      JSON.stringify({
        error: "تعذر الاتصال بخادم Ader الخلفي.",
        detail: String(error).slice(0, 500)
      }),
      {
        status: 502,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store"
        }
      }
    );
  }
}

function buildProxiedResponse(response, backend, publicUrl) {
  const responseHeaders = new Headers(response.headers);

  // Keep backend-local redirects on the public Worker origin while leaving
  // external OAuth redirects (Discord) untouched.
  const location = response.headers.get("Location");

  if (location) {
    try {
      const redirectUrl = new URL(location, backend);
      const backendUrl = new URL(backend);

      if (
        redirectUrl.protocol === backendUrl.protocol &&
        redirectUrl.hostname === backendUrl.hostname &&
        redirectUrl.port === backendUrl.port
      ) {
        redirectUrl.protocol = publicUrl.protocol;
        redirectUrl.hostname = publicUrl.hostname;
        redirectUrl.port = "";
        responseHeaders.set("Location", redirectUrl.toString());
      }
    } catch {
      // Leave malformed/unparseable Location headers unchanged.
    }
  }

  // Set-Cookie is a multi-value response header. Rebuild it explicitly so
  // the FastAPI session cookie reaches the browser on the Worker domain.
  const cookies =
    typeof response.headers.getSetCookie === "function"
      ? response.headers.getSetCookie()
      : typeof response.headers.getAll === "function"
        ? response.headers.getAll("Set-Cookie")
        : response.headers.get("Set-Cookie")
          ? [response.headers.get("Set-Cookie")]
          : [];

  responseHeaders.delete("Set-Cookie");
  for (const cookie of cookies) {
    if (cookie) responseHeaders.append("Set-Cookie", cookie);
  }

  responseHeaders.set("Cache-Control", "no-store, private");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders
  });
}
