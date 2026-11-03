"""Shared Discord guild readiness and cache helpers for the dashboard."""
from __future__ import annotations

import asyncio

import discord
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



async def resolve_bot_guild(bot, guild_id: int, *, cache_timeout: float = 5.0):
    """Resolve a fully cached guild, distinguishing absence from cache lag.

    A gateway cache miss is not proof that the bot was removed. Probe Discord's
    bot-authenticated REST endpoint first. If Discord confirms membership but
    the gateway cache has not rebuilt, return 503 rather than a false 404.
    """
    try:
        guild_id = int(guild_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="معرّف السيرفر غير صالح.") from exc

    await wait_for_bot_ready(bot)
    guild = get_cached_guild(bot, guild_id)
    if guild is not None:
        return guild

    fetch_guild = getattr(bot, "fetch_guild", None)
    if not callable(fetch_guild):
        raise HTTPException(
            status_code=503,
            detail="تعذر التحقق من عضوية البوت عبر Discord حالياً. أعد المحاولة.",
        )

    try:
        # REST is authoritative for whether this bot account can access the guild.
        await asyncio.wait_for(fetch_guild(guild_id), timeout=10.0)
    except discord.NotFound:
        return None
    except discord.Forbidden as exc:
        raise HTTPException(
            status_code=503,
            detail="رفض Discord التحقق من عضوية البوت. تعذر تأكيد حالة السيرفر حالياً.",
        ) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="تأخر رد Discord أثناء التحقق من السيرفر. أعد المحاولة.",
        ) from exc
    except discord.HTTPException as exc:
        raise HTTPException(
            status_code=503,
            detail="تعذر التحقق من عضوية البوت لدى Discord. أعد المحاولة بعد قليل.",
        ) from exc

    # Discord confirmed membership; wait for the gateway's full Guild object,
    # because dashboard modules need the real channel/role/member caches.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(cache_timeout))
    while loop.time() < deadline:
        guild = get_cached_guild(bot, guild_id)
        if guild is not None:
            return guild
        await asyncio.sleep(0.2)

    raise HTTPException(
        status_code=503,
        detail=(
            "Discord أكد أن البوت موجود في السيرفر، لكن بيانات السيرفر لم تكتمل "
            "في اتصال Gateway بعد. أعد المحاولة؛ لم يتم اعتبار السيرفر غير موجود."
        ),
    )
