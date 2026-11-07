# Ader Dashboard Backend

This folder contains the FastAPI dashboard backend used by the production bot.

## Runtime entry point

The production dashboard is started from the Discord bot through this exact chain:

```text
main.py
  -> cogs/dashboard_server.py
      -> web.dashboard_fixed_app.create_app
          -> web.dashboard_cloud.create_app
              -> web.dashboard_app.create_app
```

- `web/dashboard_app.py` owns the base FastAPI application, Discord OAuth2 flow, session handling, guild authorization, health endpoint, and core endpoints.
- `web/dashboard_cloud.py` wraps that base application and adds the Cloudflare session bridge plus the dashboard APIs for tickets, applications, teams, logs, modules, economy, analytics, Premium, and linked bots.
- `web/dashboard_fixed_app.py` is the stable import target used by `cogs/dashboard_server.py`.
- `cogs/dashboard_server.py` starts Uvicorn inside the main bot process and uses the same bot instance and SQLite database.

## Frontend and Cloudflare Worker

The production browser interface lives only in `frontend/`. The root-level `worker.js` serves those static assets through the `ASSETS` binding and proxies authentication/API routes to this FastAPI service.

Deploy from the repository root using the root `wrangler.jsonc`. Do not deploy `worker.js` alone from the Cloudflare editor, because that does not upload the `frontend/` assets or create their binding.

The public browser and API should share this origin:

```text
https://ader3.titanspeakermen027.workers.dev
```

The Discord OAuth2 redirect URI must exactly match:

```text
https://ader3.titanspeakermen027.workers.dev/callback
```

The Worker forwards `X-Forwarded-Host` and `X-Forwarded-Proto` so the backend can build redirects using the public origin. It also preserves the backend session cookie when proxying responses.

## Legacy compatibility files

- `web/api.py` and `web/api_v2.py` are compatibility imports for the base application. They are not the production import selected by `cogs/dashboard_server.py`.
- `cogs/web_dashboard.py` is a legacy alternative cog and is not included in the default production extension list in `main.py`. Do not enable it alongside `cogs/dashboard_server.py`, because that would create a second dashboard server.
- The embedded HTML dashboard in `web/dashboard_app.py` is retained for compatibility; the production Worker serves the static `frontend/` interface instead.

Keep one production path. If a new API route is added to the browser interface, implement and authorize the matching route in `web/dashboard_cloud.py` or the base app, then test it against the same running bot instance.
