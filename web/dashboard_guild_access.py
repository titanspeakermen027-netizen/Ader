"""Shared Discord guild readiness and cache helpers for the dashboard."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException


async def wait_for_bot_ready(bot, timeout: float = 12.0) -> None:
    """Wait for the Discord gateway READY state before checking its guild cache."""
    is_ready = getattr(bot, "is_ready", None)
    if callable(is_ready) and is_ready():
        return

    waiter = getattr(bot, "wait_until_ready", None)
    if not callable(waiter):
        raise HTTPException(
            status_code=503,
            detail="خدمة Discord غير جاهزة حالياً. تحقق من تشغيل ANO BOT.",
        )

    try:
        await asyncio.wait_for(waiter(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="ANO BOT يعيد الاتصال بـ Discord. انتظر اكتمال الاتصال ثم أعد المحاولة.",
        ) from exc


def get_cached_guild(bot, guild_id: int):
    """Resolve a guild from the active bot cache without trusting OAuth snapshots."""
    try:
        guild_id = int(guild_id)
    except (TypeError, ValueError):
        return None

    getter = getattr(bot, "get_guild", None)
    if callable(getter):
        try:
            guild = getter(guild_id)
            if guild is not None:
                return guild
        except Exception:
            pass

    # Discord.py's public guild collection is the fallback source of truth.
    for guild in getattr(bot, "guilds", ()) or ():
        try:
            if int(guild.id) == guild_id:
                return guild
        except (AttributeError, TypeError, ValueError):
            continue
    return None
