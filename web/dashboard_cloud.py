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
from cogs.professional_core import normalize_reaction_emojis
from utils.premium import describe as describe_premium, has_feature, FEATURES
from utils.secrets_store import TokenCipher


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

    guild = bot.get_guild(guild_id)
    if guild is None:
        raise HTTPException(status_code=404, detail="البوت غير متصل بهذا الخادم حالياً")

    key = str(guild_id)
    managed = session.get("managed_guilds", {}) or {}
    oauth_guilds = session.get("oauth_guilds", {}) or {}
    current = managed.get(key)

    if isinstance(current, dict) and base._guild_is_managed(current):
        session["active_guild_id"] = guild_id
        await base._save_session(request, bot, session)
        return guild

    user = session.get("discord_user") or {}
    try:
        user_id = int(user.get("id") or 0)
    except (TypeError, ValueError):
        user_id = 0

    allowed = bool(user_id and guild.owner_id == user_id)
    live_perms = None
    if not allowed and user_id:
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            member = None
        live_perms = getattr(member, "guild_permissions", None) if member else None
        allowed = bool(live_perms and (live_perms.administrator or live_perms.manage_guild))

    oauth_data = oauth_guilds.get(key)
    oauth_permissions = base._guild_permissions(oauth_data) if isinstance(oauth_data, dict) else 0
    if not allowed and oauth_permissions & (base.ADMINISTRATOR | base.MANAGE_GUILD):
        allowed = True

    if not allowed:
        raise HTTPException(status_code=403, detail="لا تملك صلاحية إدارة هذا الخادم")

    permissions = base.ADMINISTRATOR | base.MANAGE_GUILD if guild.owner_id == user_id else (
        base.ADMINISTRATOR if getattr(live_perms, "administrator", False)
        else base.MANAGE_GUILD
    )
    guild_data = {
        "id": guild.id,
        "name": guild.name,
        "icon": (
            (current or {}).get("icon")
            or (oauth_data or {}).get("icon")
            or (str(guild.icon.url) if guild.icon else None)
        ),
        "permissions": permissions,
        "administrator": bool(permissions & base.ADMINISTRATOR),
        "manage_guild": bool(permissions & base.MANAGE_GUILD),
    }
    managed[key] = guild_data
    session["managed_guilds"] = managed
    if isinstance(oauth_data, dict):
        oauth_guilds[key] = {**oauth_data, **guild_data}
        session["oauth_guilds"] = oauth_guilds

    session["active_guild_id"] = guild_id
    await base._save_session(request, bot, session)
    return guild


async def _require_premium(bot, guild_id: int):
    if not hasattr(bot.db, "is_server_premium") or not await bot.db.is_server_premium(guild_id):
        raise HTTPException(status_code=402, detail="⭐ هذه الميزة متوفرة حصرياً لسيرفرات Ader Premium")


async def _require_feature(bot, guild_id: int, feature: str):
    await _require_premium(bot, guild_id)
    if not await has_feature(bot.db, guild_id, feature):
        raise HTTPException(status_code=403, detail="هذه الميزة غير متاحة ضمن خطة Premium الحالية.")


def _clean_profile(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {"bot_name", "avatar_url", "banner_url", "bio", "activity_text", "activity_type"}
    profile = {k: data.get(k) for k in allowed if k in data}
    name = str(profile.get("bot_name") or "").strip()
    if name and not 2 <= len(name) <= 32:
        raise HTTPException(status_code=400, detail="اسم البوت يجب أن يكون بين 2 و32 حرفاً.")
    for key in ("avatar_url", "banner_url"):
        value = str(profile.get(key) or "").strip()
        if value and not value.startswith("https://"):
            raise HTTPException(status_code=400, detail="روابط الصور يجب أن تبدأ بـ https://")
        profile[key] = value or None
    profile["bio"] = str(profile.get("bio") or "").strip()[:1900]
    profile["activity_text"] = str(profile.get("activity_text") or "Managing your community").strip()[:128]
    profile["activity_type"] = str(profile.get("activity_type") or "watching").lower()
    if profile["activity_type"] not in {"playing", "watching", "listening", "streaming"}:
        profile["activity_type"] = "watching"
    return profile


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

    @app.get("/api/premium/plans")
    async def premium_plans(request: Request):
        session = await base._session_for(request, bot)
        if not session:
            raise HTTPException(status_code=401, detail="تسجيل الدخول مطلوب")
        return {
            "plans": [
                {
                    "id": "free",
                    "name": "Ader Free",
                    "description": "الميزات الأساسية.",
                    "features": [{"key": f.key, "name": f.name, "free_limit": f.free_limit} for f in FEATURES],
                },
                {
                    "id": "premium",
                    "name": "Ader Premium",
                    "description": "جميع ميزات Premium مع الحدود الموسعة.",
                    "features": [{"key": f.key, "name": f.name, "premium_limit": f.premium_limit} for f in FEATURES],
                },
            ]
        }

    @app.get("/api/guilds/{guild_id}/premium")
    async def cloud_premium(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        payload = await describe_premium(bot.db, guild_id)
        linked = await bot.db.get_linked_bot(guild_id) if hasattr(bot.db, "get_linked_bot") else None
        manager = getattr(bot, "linked_bot_manager", None)
        running = bool(manager and manager.linked_bot(guild_id))
        payload.update({
            "guild_id": guild_id,
            "linked_bot": {
                "configured": bool(linked),
                "running": running,
                "bot_user_id": linked.get("bot_user_id") if linked else None,
                "status": linked.get("status") if linked else "not_configured",
                "last_error": linked.get("last_error") if linked else None,
                "bot_name": linked.get("bot_name") if linked else None,
                "avatar_url": linked.get("avatar_url") if linked else None,
                "banner_url": linked.get("banner_url") if linked else None,
                "bio": linked.get("bio", "") if linked else "",
                "activity_text": linked.get("activity_text", "Managing your community") if linked else "Managing your community",
                "activity_type": linked.get("activity_type", "watching") if linked else "watching",
            },
        })
        return payload

    @app.post("/api/guilds/{guild_id}/premium/linked-bot")
    async def linked_bot_create(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        await _require_feature(bot, guild_id, "linked_bot")
        manager = getattr(bot, "linked_bot_manager", None)
        if manager is None:
            raise HTTPException(status_code=503, detail="مدير البوتات المرتبطة غير متاح حالياً.")
        if await bot.db.get_linked_bot(guild_id):
            raise HTTPException(status_code=409, detail="يوجد بوت مرتبط بهذا السيرفر. استعمل تعديل الملف أو احذف الربط أولاً.")
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="بيانات غير صالحة.")
        token = str(data.get("token") or "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="توكن البوت مطلوب.")
        try:
            cipher = TokenCipher()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        fingerprint = cipher.fingerprint(token)
        duplicate = await bot.db.get_linked_bot_by_fingerprint(fingerprint)
        if duplicate:
            raise HTTPException(status_code=409, detail="هذا البوت مرتبط أصلاً بسيرفر Premium آخر في Ader.")
        profile = _clean_profile(data)
        encrypted = cipher.encrypt(token)
        await bot.db.save_linked_bot(guild_id, encrypted, fingerprint, profile)
        try:
            result = await manager.start_for_guild(guild_id)
        except Exception as exc:
            await bot.db.delete_linked_bot(guild_id)
            raise HTTPException(status_code=400, detail=str(exc)[:500])
        return {"ok": True, **result}

    @app.put("/api/guilds/{guild_id}/premium/linked-bot")
    async def linked_bot_update(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        await _require_feature(bot, guild_id, "custom_bot_identity")
        linked = await bot.db.get_linked_bot(guild_id)
        if not linked:
            raise HTTPException(status_code=404, detail="لا يوجد بوت مرتبط بهذا السيرفر.")
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="بيانات غير صالحة.")
        profile = _clean_profile(data)
        await bot.db.update_linked_bot_profile(guild_id, profile)
        manager = getattr(bot, "linked_bot_manager", None)
        instance = manager.linked_bot(guild_id) if manager else None
        if instance and instance.user:
            updated = await bot.db.get_linked_bot(guild_id)
            from utils.linked_bot import apply_linked_profile
            try:
                await apply_linked_profile(instance, updated or {})
                await bot.db.update_linked_bot_status(guild_id, "online", None)
            except Exception:
                await bot.db.update_linked_bot_status(guild_id, "error", "تعذر تطبيق إعدادات البوت على Discord.")
                raise HTTPException(status_code=400, detail="تعذر تطبيق إعدادات البوت على Discord.")
        return {"ok": True, "linked_bot": await bot.db.get_linked_bot(guild_id)}

    @app.post("/api/guilds/{guild_id}/premium/linked-bot/start")
    async def linked_bot_start(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        await _require_feature(bot, guild_id, "linked_bot")
        manager = getattr(bot, "linked_bot_manager", None)
        if manager is None:
            raise HTTPException(status_code=503, detail="مدير البوتات المرتبطة غير متاح.")
        try:
            return await manager.start_for_guild(guild_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:500])

    @app.post("/api/guilds/{guild_id}/premium/linked-bot/stop")
    async def linked_bot_stop(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        await _require_feature(bot, guild_id, "linked_bot")
        manager = getattr(bot, "linked_bot_manager", None)
        if manager is None:
            raise HTTPException(status_code=503, detail="مدير البوتات المرتبطة غير متاح.")
        await manager.stop_for_guild(guild_id)
        return {"ok": True}

    @app.delete("/api/guilds/{guild_id}/premium/linked-bot")
    async def linked_bot_delete(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        await _require_feature(bot, guild_id, "linked_bot")
        manager = getattr(bot, "linked_bot_manager", None)
        if manager:
            await manager.stop_for_guild(guild_id)
        await bot.db.delete_linked_bot(guild_id)
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/tickets")
    async def cloud_ticket_data(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
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
        guild = await _require_guild(bot, request, guild_id)
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
        guild = await _require_guild(bot, request, guild_id)
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

    @app.get("/api/guilds/{guild_id}/core-settings")
    async def core_settings_get(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        cog = bot.get_cog("ProfessionalCore")
        if cog is not None and hasattr(cog, "settings"):
            settings = await cog.settings(guild_id)
        else:
            row = await bot.db.fetchone("SELECT data FROM ader_core_settings WHERE guild_id=?", (guild_id,))
            try:
                settings = json.loads(row["data"] or "{}") if row else {}
            except Exception:
                settings = {}
        return {"settings": settings}

    @app.put("/api/guilds/{guild_id}/core-settings")
    async def core_settings_put(request: Request, guild_id: int):
        await _require_guild(bot, request, guild_id)
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="إعدادات غير صالحة")
        cog = bot.get_cog("ProfessionalCore")
        if cog is None or not hasattr(cog, "settings") or not hasattr(cog, "save"):
            raise HTTPException(status_code=503, detail="نظام Ader Core غير متاح حالياً")
        settings = await cog.settings(guild_id)
        for section in ("logs", "automod", "autorole", "levels", "antinuke"):
            value = data.get(section)
            if isinstance(value, dict):
                settings[section].update(value)
        if not isinstance(settings.get("automod"), dict):
            settings["automod"] = {}
        settings["automod"]["mentions"] = max(2, min(50, int(settings["automod"].get("mentions", 5))))
        settings["automod"]["messages"] = max(3, min(50, int(settings["automod"].get("messages", 6))))
        settings["automod"]["window"] = max(2, min(60, int(settings["automod"].get("window", 5))))
        settings["automod"]["timeout"] = max(1, min(40320, int(settings["automod"].get("timeout", 5))))
        words = settings["automod"].get("words", [])
        settings["automod"]["words"] = [str(x).strip()[:100] for x in words if str(x).strip()][:200]
        reaction_filter = settings["automod"].get("reaction_filter")
        if not isinstance(reaction_filter, dict):
            reaction_filter = {}
        settings["automod"]["reaction_filter"] = {
            "enabled": bool(reaction_filter.get("enabled", False)),
            "timeout": max(1, min(40320, int(reaction_filter.get("timeout", 5) or 5))),
            "emojis": normalize_reaction_emojis(reaction_filter.get("emojis", [])),
        }
        settings["levels"]["min"] = max(1, min(100, int(settings["levels"].get("min", 8))))
        settings["levels"]["max"] = max(settings["levels"]["min"], min(100, int(settings["levels"].get("max", 14))))
        settings["levels"]["cooldown"] = max(5, min(3600, int(settings["levels"].get("cooldown", 45))))
        settings["levels"]["base"] = max(10, min(1000000, int(settings["levels"].get("base", 100))))
        settings["antinuke"]["threshold"] = max(2, min(20, int(settings["antinuke"].get("threshold", 4))))
        settings["antinuke"]["window"] = max(3, min(120, int(settings["antinuke"].get("window", 10))))
        if settings["antinuke"].get("action") not in {"timeout", "ban"}:
            settings["antinuke"]["action"] = "timeout"
        role_id = settings["autorole"].get("role")
        guild = bot.get_guild(guild_id)
        if role_id:
            role = guild.get_role(int(role_id)) if guild else None
            me = guild.me if guild else None
            if role is None or (me and role >= me.top_role):
                settings["autorole"]["role"] = None
        channel_id = settings["logs"].get("channel")
        if channel_id and guild and guild.get_channel(int(channel_id)) is None:
            settings["logs"]["channel"] = None
        await cog.save(guild_id, settings)
        return {"ok": True, "settings": settings}

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
