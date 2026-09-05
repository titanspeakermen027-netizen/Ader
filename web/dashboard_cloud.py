"""Cloudflare Pages adapter for Ader's existing dashboard backend.

Keeps the existing FastAPI dashboard as the source of truth while adding the
CORS/session bridge needed by the Cloudflare Worker frontend.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from web import dashboard_app as base


class CloudflareSessionBridge(BaseHTTPMiddleware):
    """Redirect backend dashboard routes to the public Cloudflare frontend."""

    async def dispatch(self, request: Request, call_next):
        frontend = os.getenv("DASHBOARD_FRONTEND_URL", "").strip().rstrip("/")

        # The public dashboard is served by the Cloudflare Worker.
        if frontend and request.url.path == "/" and request.method == "GET":
            return RedirectResponse(frontend + "/", status_code=302)

        response = await call_next(request)

        # After OAuth login/logout, send the browser back to the public URL.
        if (
            frontend
            and request.url.path in {"/callback", "/logout"}
            and response.status_code in {302, 303, 307, 308}
        ):
            location = response.headers.get("location", "")

            if location in {"", "/"}:
                response.headers["location"] = frontend + "/"

        # IMPORTANT:
        # Do not manually rewrite Set-Cookie headers here.
        # The Cloudflare Worker proxies them to the browser.
        return response


def _frontend_origins(cfg: dict[str, Any]) -> list[str]:
    """Build the list of allowed frontend origins."""

    values: list[str] = []

    configured = os.getenv(
        "DASHBOARD_FRONTEND_URL",
        "",
    ).strip().rstrip("/")

    if configured:
        values.append(configured)

    raw = cfg.get("cors_origins", [])

    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]

    if isinstance(raw, (list, tuple, set)):
        values.extend(
            str(x).strip().rstrip("/")
            for x in raw
            if str(x).strip() and str(x).strip() != "*"
        )

    return list(dict.fromkeys(values))


def _require_guild(bot, request: Request, guild_id: int):
    """Ensure the logged-in user can manage the requested guild."""

    session = base._session_for(request)

    if not session:
        raise HTTPException(
            status_code=401,
            detail="تسجيل الدخول مطلوب",
        )

    managed = session.get("managed_guilds", {}) or {}

    data = managed.get(str(guild_id))

    if (
        not isinstance(data, dict)
        or not base._guild_is_managed(data)
    ):
        raise HTTPException(
            status_code=403,
            detail="لا تملك صلاحية إدارة هذا الخادم",
        )

    guild = bot.get_guild(guild_id)

    if guild is None:
        raise HTTPException(
            status_code=404,
            detail="البوت غير متصل بهذا الخادم حالياً",
        )

    return guild


def create_app(bot):
    """Create the Cloudflare-compatible Ader dashboard application."""

    cfg = bot.config.get("web", {}) or {}

    # The original dashboard remains the main application.
    app = base.create_app(bot)

    origins = _frontend_origins(cfg)

    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=[
                "GET",
                "POST",
                "PUT",
                "DELETE",
                "OPTIONS",
            ],
            allow_headers=["*"],
        )

    # Add redirect handling after the original dashboard middleware.
    app.add_middleware(CloudflareSessionBridge)

    @app.get("/api/guilds/{guild_id}/tickets")
    async def cloud_ticket_panels(
        request: Request,
        guild_id: int,
    ):
        _require_guild(bot, request, guild_id)

        panels = await bot.db.list_ticket_panels(guild_id)

        rows = await bot.db.fetchall(
            """
            SELECT
                id,
                channel_id,
                user_id,
                status,
                claimed_by,
                created_at
            FROM tickets
            WHERE guild_id=?
            ORDER BY id DESC
            LIMIT 100
            """,
            (guild_id,),
        )

        return {
            "panels": panels,
            "tickets": [dict(row) for row in rows],
        }

    @app.post("/api/guilds/{guild_id}/tickets/panels")
    async def cloud_ticket_panel_create(
        request: Request,
        guild_id: int,
    ):
        guild = _require_guild(bot, request, guild_id)

        data = await request.json()

        try:
            channel = guild.get_channel(
                int(data.get("channel_id", 0))
            )

            category = guild.get_channel(
                int(data.get("category_id", 0))
            )

        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="القناة أو الفئة غير صالحة",
            )

        import discord

        if not isinstance(channel, discord.TextChannel):
            raise HTTPException(
                status_code=400,
                detail="القناة غير صالحة",
            )

        if not isinstance(category, discord.CategoryChannel):
            raise HTTPException(
                status_code=400,
                detail="الفئة غير صالحة",
            )

        cog = bot.get_cog("TicketManager")

        if not cog:
            raise HTTPException(
                status_code=503,
                detail="نظام التذاكر غير محمّل",
            )

        options = data.get("options") or [
            {
                "name": "فتح تذكرة",
                "emoji": "🎫",
                "ticket_name": "ticket-{user}",
                "description": "فتح تذكرة",
            }
        ]

        panel_data = {
            "guild_id": guild_id,
            "channel_id": channel.id,
            "title": str(
                data.get("title")
                or "🎫 الدعم الفني"
            ),
            "description": str(
                data.get("description")
                or "اختار القسم المناسب لفتح تذكرة."
            ),
            "image_url": data.get("image_url"),
            "mode": str(
                data.get("mode")
                or "buttons"
            ),
            "category_id": category.id,
            "support_role_id": data.get(
                "support_role_id"
            ),
            "ticket_description": str(
                data.get("ticket_description")
                or "شرح لينا المشكل بالتفصيل."
            ),
            "options": options,
        }

        panel_id = await bot.db.create_ticket_panel(
            panel_data
        )

        panel = await bot.db.get_ticket_panel(
            panel_id
        )

        return {
            "ok": True,
            "panel": panel,
        }

    return app
