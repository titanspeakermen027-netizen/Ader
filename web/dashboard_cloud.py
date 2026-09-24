"""Cloudflare Pages adapter for Ader's existing dashboard backend.

Keeps the existing FastAPI dashboard as the source of truth while adding the
CORS/session bridge needed by the Cloudflare Worker frontend.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import discord

from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from web import dashboard_app as base


class CloudflareSessionBridge(BaseHTTPMiddleware):
    """Redirect backend dashboard routes to the public Cloudflare frontend."""

    async def dispatch(self, request: Request, call_next):
        frontend = os.getenv("DASHBOARD_FRONTEND_URL", "").strip().rstrip("/")
        if frontend and request.url.path == "/" and request.method == "GET":
            return RedirectResponse(frontend + "/", status_code=302)
        response = await call_next(request)
        if (
            frontend
            and request.url.path in {"/callback", "/logout"}
            and response.status_code in {302, 303, 307, 308}
        ):
            location = response.headers.get("location", "")
            if location in {"", "/"}:
                response.headers["location"] = frontend + "/"
        return response


def _frontend_origins(cfg: dict[str, Any]) -> list[str]:
    values: list[str] = []
    configured = os.getenv("DASHBOARD_FRONTEND_URL", "").strip().rstrip("/")
    if configured:
        values.append(configured)
    raw = cfg.get("cors_origins", [])
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]
    if isinstance(raw, (list, tuple, set)):
        values.extend(str(x).strip().rstrip("/") for x in raw if str(x).strip() and str(x).strip() != "*")
    return list(dict.fromkeys(values))


async def _require_guild(bot, request: Request, guild_id: int):
    session = await base._session_for(request, bot)
    if not session:
        raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
    managed = session.get("managed_guilds", {}) or {}
    data = managed.get(str(guild_id))
    if not isinstance(data, dict) or not base._guild_is_managed(data):
        raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise HTTPException(status_code=404, detail="البوت غير متصل بهذا الخادم حالياً")
    return guild


async def _require_premium(bot, guild_id: int):
    if not hasattr(bot.db, "is_server_premium") or not await bot.db.is_server_premium(guild_id):
        raise HTTPException(status_code=402, detail="⭐ هذه الميزة متوفرة حصرياً لسيرفرات Ader Premium")


def create_app(bot):
    cfg = bot.config.get("web", {}) or {}
    app = base.create_app(bot)
    origins = _frontend_origins(cfg)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )
    app.add_middleware(CloudflareSessionBridge)

    @app.get("/api/guilds/{guild_id}/premium")
    async def cloud_premium(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        premium = await bot.db.get_server_premium(guild_id) if hasattr(bot.db, "get_server_premium") else None
        return {
            "active": bool(premium),
            "guild_id": guild_id,
            "expires_at": premium.get("expires_at") if premium else None,
            "started_at": premium.get("started_at") if premium else None,
        }

    @app.get("/api/guilds/{guild_id}/tickets")
    async def cloud_ticket_data(request: Request, guild_id: int):
        _require_guild(bot, request, guild_id)
        await _require_premium(bot, guild_id)
        panels = await bot.db.list_ticket_panels(guild_id)
        settings = await bot.db.get_ticket_settings(guild_id)
        rows = await bot.db.fetchall(
            "SELECT id, channel_id, user_id, status, claimed_by, created_at, closed_at, data "
            "FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT 100", (guild_id,)
        )
        tickets = []
        for row in rows:
            item = dict(row)
            try:
                item["data"] = json.loads(item.get("data") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["data"] = {}
            item["rating"] = await bot.db.get_ticket_rating(int(item["id"])) if hasattr(bot.db, "get_ticket_rating") else None
            tickets.append(item)
        return {"settings": settings, "panels": panels, "tickets": tickets}

    @app.put("/api/guilds/{guild_id}/tickets/settings")
    async def cloud_ticket_settings(request: Request, guild_id: int):
        guild = _require_guild(bot, request, guild_id)
        await _require_premium(bot, guild_id)
        data = await request.json()
        current = merge_ticket_settings(data, await bot.db.get_ticket_settings(guild_id))
        for key in ("default_category_id", "transcript_channel_id", "log_channel_id"):
            value = current.get(key)
            if value:
                channel = guild.get_channel(int(value))
                if not isinstance(channel, discord.TextChannel) and key != "default_category_id":
                    current[key] = None
        category_id = current.get("default_category_id")
        if category_id:
            category = guild.get_channel(int(category_id))
            if not isinstance(category, discord.CategoryChannel):
                current["default_category_id"] = None
        role_id = current.get("default_support_role_id")
        if role_id and guild.get_role(int(role_id)) is None:
            current["default_support_role_id"] = None
        saved = await bot.db.save_ticket_settings(guild_id, current)
        return {"ok": True, "settings": saved}

    @app.post("/api/guilds/{guild_id}/tickets/panels")
    async def cloud_ticket_panel_create(request: Request, guild_id: int):
        guild = _require_guild(bot, request, guild_id)
        await _require_premium(bot, guild_id)
        data = await request.json()
        panel = normalise_panel_payload(guild, data)
        panel["guild_id"] = guild_id
        panel_id = await bot.db.create_ticket_panel(panel)
        panel = await bot.db.get_ticket_panel(panel_id)
        cog = bot.get_cog("TicketManager")
        try:
            if data.get("publish"):
                panel = await cog.publish_panel(panel_id) if cog else panel
        except Exception:
            await bot.db.delete_ticket_panel(panel_id)
            raise
        return {"ok": True, "panel": panel}

    @app.put("/api/guilds/{guild_id}/tickets/panels/{panel_id}")
    async def cloud_ticket_panel_update(request: Request, guild_id: int, panel_id: int):
        guild = _require_guild(bot, request, guild_id)
        await _require_premium(bot, guild_id)
        existing = await bot.db.get_ticket_panel(panel_id)
        if not existing or int(existing["guild_id"]) != guild_id:
            raise HTTPException(status_code=404, detail="لوحة التذاكر غير موجودة.")
        data = await request.json()
        payload = normalise_panel_payload(guild, data)
        payload.pop("guild_id", None)
        if not await bot.db.update_ticket_panel(panel_id, payload):
            raise HTTPException(status_code=500, detail="تعذر حفظ لوحة التذاكر.")
        cog = bot.get_cog("TicketManager")
        if cog and data.get("publish"):
            try:
                await cog.publish_panel(panel_id)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "panel": await bot.db.get_ticket_panel(panel_id)}

    @app.delete("/api/guilds/{guild_id}/tickets/panels/{panel_id}")
    async def cloud_ticket_panel_delete(request: Request, guild_id: int, panel_id: int):
        guild = _require_guild(bot, request, guild_id)
        await _require_premium(bot, guild_id)
        panel = await bot.db.get_ticket_panel(panel_id)
        if not panel or int(panel["guild_id"]) != guild_id:
            raise HTTPException(status_code=404, detail="لوحة التذاكر غير موجودة.")
        channel = guild.get_channel(int(panel.get("channel_id") or 0))
        if isinstance(channel, discord.TextChannel) and panel.get("message_id"):
            try:
                message = await channel.fetch_message(int(panel["message_id"]))
                await message.delete()
            except (discord.NotFound, discord.HTTPException):
                pass
        await bot.db.delete_ticket_panel(panel_id)
        return {"ok": True}

    return app


def merge_ticket_settings(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    base = {
        "enabled": True, "default_category_id": None, "default_support_role_id": None,
        "transcript_channel_id": None, "log_channel_id": None, "claim_enabled": True,
        "rating_enabled": True, "allow_user_close": True, "allow_user_reopen": False,
        "allow_member_add": True, "allow_member_remove": True, "allow_rename": True,
        "allow_lock": True, "keep_closed": True, "delete_after_close_seconds": 0,
        "max_open_per_user": 1, "channel_name_template": "ticket-{number}-{user}",
    }
    base.update(current or {})
    for key in base:
        if key in payload:
            base[key] = payload[key]
    for key in ("enabled","claim_enabled","rating_enabled","allow_user_close","allow_user_reopen","allow_member_add","allow_member_remove","allow_rename","allow_lock","keep_closed"):
        base[key] = bool(base[key])
    try: base["max_open_per_user"] = max(1, min(10, int(base["max_open_per_user"])))
    except (TypeError, ValueError): base["max_open_per_user"] = 1
    try: base["delete_after_close_seconds"] = max(0, min(3600, int(base["delete_after_close_seconds"])))
    except (TypeError, ValueError): base["delete_after_close_seconds"] = 0
    return base


def normalise_panel_payload(guild, data: dict[str, Any]) -> dict[str, Any]:
    import discord
    title = str(data.get("title") or "الدعم الفني").strip()[:256]
    description = str(data.get("description") or "اختر نوع الطلب لفتح تذكرة.").strip()[:4096]
    mode = str(data.get("mode") or "buttons").lower()
    if mode not in {"buttons","select"}: mode = "buttons"
    channel_id = as_resource_int(data.get("channel_id"))
    category_id = as_resource_int(data.get("category_id"))
    role_id = as_resource_int(data.get("support_role_id"))
    channel = guild.get_channel(channel_id or 0)
    category = guild.get_channel(category_id or 0)
    role = guild.get_role(role_id) if role_id else None
    if not isinstance(channel, discord.TextChannel): raise HTTPException(status_code=400, detail="قناة نشر اللوحة غير صالحة.")
    if not isinstance(category, discord.CategoryChannel): raise HTTPException(status_code=400, detail="فئة التذاكر غير صالحة.")
    if role_id and role is None: raise HTTPException(status_code=400, detail="رتبة الدعم غير صالحة.")
    image_url = safe_panel_url(data.get("image_url"))
    raw_options = data.get("options") or []
    if not isinstance(raw_options, list): raw_options = []
    options = []
    for raw in raw_options[:25]:
        if not isinstance(raw, dict): continue
        option = {
            "name": str(raw.get("name") or "فتح تذكرة").strip()[:80],
            "emoji": str(raw.get("emoji") or "🎫").strip()[:20],
            "description": str(raw.get("description") or "فتح تذكرة").strip()[:100],
            "ticket_name": str(raw.get("ticket_name") or "ticket-{number}-{user}").strip()[:90],
            "category_id": as_resource_int(raw.get("category_id")) or category_id,
            "support_role_id": as_resource_int(raw.get("support_role_id")) or role_id,
            "color": safe_color(raw.get("color"), "#5865F2"),
            "image_url": safe_panel_url(raw.get("image_url")),
            "footer": str(raw.get("footer") or "Ader Support").strip()[:100],
            "priority": str(raw.get("priority") or "normal")[:20],
            "max_open": max(1, min(10, int(raw.get("max_open", 1) or 1))),
            "button_style": str(raw.get("button_style") or "primary").lower(),
            "enabled": bool(raw.get("enabled", True)),
        }
        if option["category_id"] and not isinstance(guild.get_channel(int(option["category_id"])), discord.CategoryChannel):
            option["category_id"] = category_id
        if option["support_role_id"] and guild.get_role(int(option["support_role_id"])) is None:
            option["support_role_id"] = role_id
        if option["button_style"] not in {"primary","secondary","success","danger"}: option["button_style"] = "primary"
        options.append(option)
    if not options:
        options = [{"name":"الدعم العام","emoji":"🎫","description":"فتح تذكرة دعم","ticket_name":"ticket-{number}-{user}","category_id":category_id,"support_role_id":role_id,"color":"#5865F2","image_url":None,"footer":"Ader Support","priority":"normal","max_open":1,"button_style":"primary","enabled":True}]
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    settings = {
        "color": safe_color(settings.get("color"), "#5865F2"),
        "thumbnail_url": safe_panel_url(settings.get("thumbnail_url")),
        "footer": str(settings.get("footer") or "Ader Support").strip()[:100],
        "select_placeholder": str(settings.get("select_placeholder") or "اختر نوع التذكرة").strip()[:100],
        "ticket_footer": str(settings.get("ticket_footer") or "Ader Support").strip()[:100],
        "ticket_image_url": safe_panel_url(settings.get("ticket_image_url")),
    }
    return {
        "channel_id": channel_id, "message_id": as_resource_int(data.get("message_id")),
        "title": title, "description": description, "image_url": image_url, "mode": mode,
        "button_label": "فتح تذكرة", "button_emoji": "🎫", "category_id": category_id,
        "support_role_id": role_id, "ticket_description": str(data.get("ticket_description") or "يرجى شرح المشكلة بالتفصيل.").strip()[:2000],
        "options": options, "settings": settings,
    }


def as_resource_int(value: Any) -> int | None:
    try: return int(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError): return None


def safe_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", text): return text
    return fallback


def safe_panel_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text: return None
    return text[:1000] if text.startswith(("https://","http://")) else None
