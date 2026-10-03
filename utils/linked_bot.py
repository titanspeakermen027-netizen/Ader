"""Manager for Discord bots linked to Ader Premium servers."""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

import discord
from discord.ext import commands

from utils.secrets_store import TokenCipher


class LinkedBotManager:
    """Starts and stops per-server custom bot instances.

    Every linked instance points at the exact same SQLite file used by the
    primary Ader process, so guild/user/ticket/economy settings are shared.
    """

    def __init__(self, primary_bot):
        self.primary = primary_bot
        self.log = logging.getLogger("Ader.linked_bots")
        self.instances: dict[int, commands.Bot] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.cipher = None
        self.monitor_task: asyncio.Task | None = None

    async def start_monitor(self):
        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(self._monitor(), name="ader-linked-bot-monitor")

    async def _monitor(self):
        while True:
            try:
                await asyncio.sleep(30)
                for guild_id in list(self.instances):
                    try:
                        if not await self.primary.db.is_server_premium(guild_id):
                            self.log.info("Premium expired; stopping linked bot for guild %s", guild_id)
                            await self.stop_for_guild(guild_id)
                    except Exception as exc:
                        self.log.error("Linked bot monitor failed for guild %s: %s", guild_id, str(exc)[:300])
            except asyncio.CancelledError:
                return

    def is_handoff_guild(self, guild_id: int) -> bool:
        return int(guild_id) in self.instances

    def linked_bot(self, guild_id: int):
        return self.instances.get(int(guild_id))

    async def start_for_guild(self, guild_id: int) -> dict[str, Any]:
        guild_id = int(guild_id)
        existing = self.instances.get(guild_id)
        if existing and not existing.is_closed():
            return {"ok": True, "already_running": True, "bot_user_id": existing.user.id if existing.user else None}

        record = await self.primary.db.get_linked_bot(guild_id)
        if not record:
            raise RuntimeError("لا يوجد بوت مرتبط بهذا السيرفر.")
        if not await self.primary.db.is_server_premium(guild_id):
            raise RuntimeError("البوت المرتبط متاح فقط لسيرفرات Premium.")
        try:
            cipher = TokenCipher()
            token = cipher.decrypt(str(record["encrypted_token"]))
        except Exception as exc:
            raise RuntimeError("تعذر فك تشفير توكن البوت. تأكد من إعداد ADER_TOKEN_ENCRYPTION_KEY.") from exc

        from main import Ader

        config = copy.deepcopy(self.primary.config)
        config.setdefault("web", {})["enabled"] = False
        bot = Ader(
            config,
            linked_mode=True,
            linked_guild_id=guild_id,
            db_path=str(self.primary.db.path),
        )
        bot.linked_manager = self
        bot._linked_owner_bot = self.primary

        async def runner():
            try:
                await bot.start(token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                safe = str(exc).replace(token, "[REDACTED]")[:500]
                self.log.error("Linked bot failed for guild %s: %s", guild_id, safe)
                await self.primary.db.update_linked_bot_status(guild_id, "error", safe)
                raise
            finally:
                self.instances.pop(guild_id, None)
                self.tasks.pop(guild_id, None)
                try:
                    await self.primary.db.update_linked_bot_runtime(guild_id, None)
                except Exception:
                    pass

        task = asyncio.create_task(runner(), name=f"ader-linked-bot-{guild_id}")
        self.instances[guild_id] = bot
        self.tasks[guild_id] = task

        for _ in range(240):
            if bot.is_ready():
                break
            if task.done():
                exc = task.exception()
                if exc:
                    self.instances.pop(guild_id, None)
                    self.tasks.pop(guild_id, None)
                    raise RuntimeError(f"فشل تشغيل البوت المرتبط: {str(exc)[:350]}")
                break
            await asyncio.sleep(0.1)

        if not bot.is_ready():
            await self.stop_for_guild(guild_id)
            raise RuntimeError("انتهت مهلة تشغيل البوت المرتبط. تحقق من التوكن ومن اتصال البوت بالسيرفر.")
        if bot.get_guild(guild_id) is None:
            await self.stop_for_guild(guild_id)
            raise RuntimeError("البوت اشتغل لكن ما لقيتش راسو داخل السيرفر. زيد البوت للسيرفر أولاً ثم عاود الربط.")

        await self.primary.db.update_linked_bot_runtime(guild_id, bot.user.id)
        await self.primary.db.update_linked_bot_status(guild_id, "online", None)
        return {"ok": True, "already_running": False, "bot_user_id": bot.user.id, "bot_name": bot.user.name}

    async def stop_for_guild(self, guild_id: int) -> None:
        guild_id = int(guild_id)
        bot = self.instances.pop(guild_id, None)
        task = self.tasks.pop(guild_id, None)
        if bot is not None:
            try:
                await bot.close()
            except Exception:
                pass
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.primary.db.update_linked_bot_status(guild_id, "stopped", None)
            await self.primary.db.update_linked_bot_runtime(guild_id, None)
        except Exception:
            pass

    async def stop_all(self) -> None:
        for guild_id in list(self.instances):
            await self.stop_for_guild(guild_id)
        if self.monitor_task is not None and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        self.monitor_task = None


async def apply_linked_profile(bot, profile: dict[str, Any]) -> None:
    """Apply supported Discord bot profile fields.

    Discord.py exposes username/avatar/banner for ClientUser.edit. Bot bio is
    intentionally stored by Ader but is not sent through an unsupported API.
    """
    changes: dict[str, Any] = {}
    username = str(profile.get("bot_name") or "").strip()
    if username:
        changes["username"] = username

    async def fetch_image(url: str):
        import aiohttp
        value = str(url or "").strip()
        if not value or not value.startswith(("https://", "http://")):
            return None
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(value) as response:
                if response.status >= 400:
                    raise RuntimeError(f"تعذر تحميل الصورة ({response.status}).")
                content = await response.read()
                if len(content) > 8 * 1024 * 1024:
                    raise RuntimeError("الصورة أكبر من 8MB.")
                return content

    avatar_url = str(profile.get("avatar_url") or "").strip()
    banner_url = str(profile.get("banner_url") or "").strip()
    if avatar_url:
        changes["avatar"] = await fetch_image(avatar_url)
    if banner_url:
        changes["banner"] = await fetch_image(banner_url)

    if changes:
        await bot.user.edit(**changes)

    activity_text = str(profile.get("activity_text") or "Managing your community").strip()[:128]
    activity_type = str(profile.get("activity_type") or "watching").lower()
    activity_types = {
        "playing": discord.ActivityType.playing,
        "watching": discord.ActivityType.watching,
        "listening": discord.ActivityType.listening,
        "streaming": discord.ActivityType.streaming,
    }
    await bot.change_presence(
        status=discord.Status(str(profile.get("status") or "online").lower()),
        activity=discord.Activity(type=activity_types.get(activity_type, discord.ActivityType.watching), name=activity_text),
    )
