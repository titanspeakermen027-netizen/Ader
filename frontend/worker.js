export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Static dashboard files
    if (url.pathname !== "/login" &&
        !url.pathname.startsWith("/api/") &&
        url.pathname !== "/callback" &&
        url.pathname !== "/logout") {
      return env.ASSETS.fetch(request);
    }

    // Proxy backend routes through the Worker
    const backend = "http://nova.hattena.com:25979";
    const target = new URL(url.pathname + url.search, backend);

    return fetch(new Request(target, request));
  }
};
