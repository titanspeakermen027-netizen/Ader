# Ader Dashboard — Cloudflare Workers

The Ader dashboard uses Cloudflare Workers Static Assets for the frontend and a separate FastAPI service for the Discord bot/API.

The Cloudflare Worker lives at the repository root (worker.js). The frontend/ directory contains only browser assets.

## Architecture

Browser → Cloudflare Worker → FastAPI backend

The same Worker origin serves:

- static frontend files
- /login, /callback, /logout
- /api/*
- /healthz

This keeps the dashboard and OAuth session on one public origin and avoids cross-origin cookie problems.

## Cloudflare deployment

This repository includes wrangler.jsonc with the required Workers Static Assets configuration:

- Worker entry point: ./worker.js
- Static assets directory: ./frontend
- Assets binding: ASSETS
- SPA fallback: single-page-application
- Backend routes run through the Worker first
- Backend URL: http://nova.hatenna.com:25979

### Deploy with Wrangler

From the repository root:

~~~bash
npx wrangler deploy
~~~

Use the repository wrangler.jsonc when deploying. Do not paste the old frontend/worker.js into a standalone Worker without the Static Assets configuration.

### Cloudflare Dashboard

When configuring the Worker from the Cloudflare dashboard, make sure the Worker is deployed with a Static Assets collection pointing to:

~~~text
frontend
~~~

and that the binding name is exactly:

~~~text
ASSETS
~~~

Without that binding, browser requests that reach the static frontend can fail with:

~~~text
TypeError: Cannot read properties of undefined (reading 'fetch')
~~~

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

The FastAPI backend already reads the public origin from X-Forwarded-Host / X-Forwarded-Proto, which the Worker supplies for OAuth requests.

## Worker backend variable

The Worker uses:

~~~text
BACKEND_URL=http://nova.hatenna.com:25979
~~~

This value is stored in wrangler.jsonc and can also be managed as a Cloudflare Worker environment variable.

## Login flow

1. The browser opens the dashboard on the Worker domain.
2. /login is proxied to FastAPI.
3. FastAPI creates OAuth state and redirects to Discord.
4. Discord returns to /callback on the same Worker domain.
5. The Worker forwards the callback to FastAPI and preserves Set-Cookie.
6. FastAPI creates the dashboard session.
7. The browser is redirected to /.
8. The frontend reads /api/me using the same origin.

## Frontend configuration

frontend/config.js intentionally keeps:

~~~js
API_BASE: ""
~~~

A relative API base keeps the dashboard API and OAuth session on the same public origin.

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
