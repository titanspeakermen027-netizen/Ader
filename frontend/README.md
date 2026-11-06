# Ader Dashboard — Cloudflare Workers

The Ader dashboard has one canonical browser UI in `frontend/`. Cloudflare Workers Static Assets serves those files, while the FastAPI service is the single backend for Discord OAuth, sessions, and dashboard APIs. The separate HTML dashboard embedded in FastAPI is not the production UI.

## Single source of truth

**Deploy this Worker from the repository root.** The only Wrangler deployment configuration is:

- `./wrangler.jsonc` — repository root
- `./worker.js` — Worker entry point
- `./frontend/` — static browser assets
- Binding name — exactly `ASSETS`

Do not use a Wrangler configuration from inside `frontend/`, and do not paste `worker.js` alone into the Cloudflare dashboard editor and deploy it as a standalone script. A standalone script deployment does not upload the `frontend/` directory or create the `ASSETS` binding, so the Worker returns the "missing the ASSETS binding" error.

## Architecture

Browser → Cloudflare Worker → FastAPI backend

The same Worker origin serves:

- the canonical dashboard UI from `frontend/index.html` and its CSS/JS assets through `env.ASSETS.fetch()`
- `/login`, `/callback`, `/logout` through FastAPI
- `/api/*` through FastAPI
- `/healthz` through FastAPI

This keeps the dashboard and OAuth session on one public origin and avoids cross-origin cookie problems.

## Cloudflare deployment

### Deploy with Wrangler

Run from the repository root (the folder that contains `worker.js` and `wrangler.jsonc`):

~~~bash
npx wrangler deploy --config ./wrangler.jsonc
~~~

Wrangler reads the root configuration, uploads the Worker and the contents of `./frontend`, and creates the `ASSETS` binding used by `worker.js`.

### Deploy through Cloudflare Workers Builds

When connecting this repository to Cloudflare Workers Builds, configure the project so that:

- Root directory is the repository root (`.`), not `frontend/`.
- Deploy command is `npx wrangler deploy --config ./wrangler.jsonc`.
- The deployment uses the root `wrangler.jsonc`.

Do not deploy only the Worker source from the dashboard editor. The Static Assets collection must be included in the same deployment as the Worker script.

## Frontend and routing configuration

The root `wrangler.jsonc` configures:

- Worker entry point: `./worker.js`
- Static assets directory: `./frontend`
- Assets binding: `ASSETS`
- SPA fallback: `single-page-application`
- Worker-first routes: `/healthz`, `/login`, `/callback`, `/logout`, and `/api/*`
- Backend URL: `http://nova.hatenna.com:25979`

The production browser UI is exclusively `frontend/`. Do not switch the Worker to the embedded FastAPI HTML dashboard; FastAPI remains the OAuth/API backend only.

The frontend configuration in `frontend/config.js` intentionally keeps:

~~~js
API_BASE: ""
~~~

A relative API base keeps the dashboard API and OAuth session on the same public origin.

## Backend environment

On the Python/FastAPI host, keep the existing Discord OAuth2 and dashboard session environment variables configured.

The public callback must be:

~~~text
https://ader3.titanspeakermen027.workers.dev/callback
~~~

The public frontend URL must be:

~~~text
https://ader3.titanspeakermen027.workers.dev
~~~

The callback URL must exactly match the OAuth2 Redirect URI configured in the Discord Developer Portal.

The FastAPI backend reads the public origin from `X-Forwarded-Host` and `X-Forwarded-Proto`, which the Worker supplies for OAuth requests.

## Worker backend variable

The Worker uses:

~~~text
BACKEND_URL=http://nova.hatenna.com:25979
~~~

This value is stored in the root `wrangler.jsonc` and can also be managed as a Cloudflare Worker environment variable. Keep the Wrangler configuration as the source of truth for deployment.

## Login flow

1. The browser opens the dashboard on the Worker domain.
2. `/login` is proxied to FastAPI.
3. FastAPI creates OAuth state and redirects to Discord.
4. Discord returns to `/callback` on the same Worker domain.
5. The Worker forwards the callback to FastAPI and preserves `Set-Cookie`.
6. FastAPI creates the dashboard session.
7. The browser is redirected to `/`.
8. The frontend reads `/api/me` using the same origin.

## Features

- Discord OAuth2 login
- Server selector
- Responsive dark/light UI
- Overview statistics and Ader health
- Slash-command controls
- Shortcut alias management
- Ticket panel creation/publishing
- Ticket list
- Verified Teams view
- Server roles/channels browser
- Mobile navigation
- Arabic/French UI direction toggle
- AutoMod forbidden-reaction controls
