"""Manager for user-owned Discord bots with server allowlists."""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

import discord
from discord.ext import commands

from utils.secrets_store import TokenCipher


class LinkedBotManager:
    """Run one linked bot per Premium account, restricted to selected guilds."""

    def __init__(self, primary_bot):
        self.primary = primary_bot
        self.log = logging.getLogger("Ader.linked_bots")
        self.instances: dict[int, commands.Bot] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.monitor_task: asyncio.Task | None = None
        self._locks: dict[int, asyncio.Lock] = {}

    async def start_monitor(self):
        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(
                self._monitor(),
                name="ader-linked-bot-account-monitor",
            )

    async def _monitor(self):
        try:
            await self.primary.wait_until_ready()
            # Restore accounts that were online before a process restart.
            records = await self.primary.db.list_linked_bot_accounts()
            for record in records:
                if str(record.get("status") or "") not in {"online", "starting"}:
                    continue
                owner_id = int(record["owner_id"])
                if not await self.primary.db.is_user_premium(owner_id):
                    await self.primary.db.update_linked_bot_account_status(
                        owner_id, "stopped", "انتهى اشتراك Premium للمستخدم.",
                    )
                    continue
                try:
                    await self.start_for_user(owner_id)
                except Exception as exc:
                    self.log.error(
                        "Could not restore linked bot for owner %s: %s",
                        owner_id,
                        str(exc)[:400],
                    )

            while True:
                await asyncio.sleep(30)
                for owner_id in list(self.instances):
                    try:
                        if not await self.primary.db.is_user_premium(owner_id):
                            self.log.info("User Premium expired; stopping linked bot for %s", owner_id)
                            await self.stop_for_user(owner_id)
                    except Exception as exc:
                        self.log.error(
                            "Linked bot Premium monitor failed for %s: %s",
                            owner_id,
                            str(exc)[:300],
                        )
        except asyncio.CancelledError:
            return

    def is_handoff_guild(self, guild_id: int) -> bool:
        guild_id = int(guild_id)
        return any(
            guild_id in set(getattr(bot, "linked_guild_ids", set()))
            for bot in self.instances.values()
        )

    def linked_bot(self, owner_id: int):
        return self.instances.get(int(owner_id))

    async def start_for_user(self, owner_id: int) -> dict[str, Any]:
        owner_id = int(owner_id)
        lock = self._locks.setdefault(owner_id, asyncio.Lock())
        async with lock:
            existing = self.instances.get(owner_id)
            if existing and not existing.is_closed():
                return {
                    "ok": True,
                    "already_running": True,
                    "bot_user_id": existing.user.id if existing.user else None,
                    "joined_guild_ids": [g.id for g in existing.guilds],
                }

            if not await self.primary.db.is_user_premium(owner_id):
                raise RuntimeError("Linked Bot متاح فقط لحسابات Ader Premium الفعالة.")
            record = await self.primary.db.get_linked_bot_account(owner_id)
            if not record:
                raise RuntimeError("لم يتم ربط بوت بهذا الحساب بعد.")
            allowed_ids = {
                int(guild_id)
                for guild_id in (record.get("allowed_guild_ids") or [])
                if int(guild_id) > 0
            }
            if not allowed_ids:
                raise RuntimeError("حدد سيرفراً واحداً على الأقل قبل تشغيل Linked Bot.")

            try:
                token = TokenCipher().decrypt(str(record["encrypted_token"]))
            except Exception as exc:
                raise RuntimeError(
                    "تعذر فك تشفير توكن البوت. تحقق من ADER_TOKEN_ENCRYPTION_KEY."
                ) from exc

            from main import Ader

            config = copy.deepcopy(self.primary.config)
            config.setdefault("web", {})["enabled"] = False
            bot = Ader(
                config,
                linked_mode=True,
                linked_guild_ids=allowed_ids,
                linked_owner_id=owner_id,
                db_path=str(self.primary.db.path),
            )
            bot.linked_manager = self
            bot._linked_owner_bot = self.primary

            await self.primary.db.update_linked_bot_account_status(owner_id, "starting", None)

            async def runner():
                token_value = token
                try:
                    await bot.start(token_value)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    safe = str(exc).replace(token_value, "[REDACTED]")[:500]
                    self.log.error("Linked bot failed for owner %s: %s", owner_id, safe)
                    await self.primary.db.update_linked_bot_account_status(owner_id, "error", safe)
                    raise
                finally:
                    if self.instances.get(owner_id) is bot:
                        self.instances.pop(owner_id, None)
                    if self.tasks.get(owner_id) is asyncio.current_task():
                        self.tasks.pop(owner_id, None)
                    try:
                        await self.primary.db.update_linked_bot_account_runtime(owner_id, None)
                    except Exception:
                        pass

            task = asyncio.create_task(runner(), name=f"ader-linked-bot-user-{owner_id}")
            self.instances[owner_id] = bot
            self.tasks[owner_id] = task

            for _ in range(300):
                if bot.is_ready():
                    break
                if task.done():
                    try:
                        exc = task.exception()
                    except asyncio.CancelledError:
                        exc = RuntimeError("تم إيقاف تشغيل البوت.")
                    self.instances.pop(owner_id, None)
                    self.tasks.pop(owner_id, None)
                    if exc:
                        raise RuntimeError(f"فشل تشغيل البوت المرتبط: {str(exc)[:350]}")
                    break
                await asyncio.sleep(0.1)

            if not bot.is_ready():
                await self.stop_for_user(owner_id)
                raise RuntimeError("انتهت مهلة تشغيل البوت المرتبط. تحقق من التوكن واتصال Discord.")

            await self.primary.db.update_linked_bot_account_runtime(
                owner_id,
                bot.user.id if bot.user else None,
            )
            await self.primary.db.update_linked_bot_account_status(owner_id, "online", None)
            return {
                "ok": True,
                "already_running": False,
                "bot_user_id": bot.user.id if bot.user else None,
                "bot_name": bot.user.name if bot.user else None,
                "joined_guild_ids": [guild.id for guild in bot.guilds if guild.id in allowed_ids],
                "allowed_guild_ids": sorted(allowed_ids),
            }

    async def stop_for_user(self, owner_id: int) -> None:
        owner_id = int(owner_id)
        lock = self._locks.setdefault(owner_id, asyncio.Lock())
        async with lock:
            bot = self.instances.pop(owner_id, None)
            task = self.tasks.pop(owner_id, None)
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
                await self.primary.db.update_linked_bot_account_status(owner_id, "stopped", None)
                await self.primary.db.update_linked_bot_account_runtime(owner_id, None)
            except Exception:
                pass

    async def restart_for_user(self, owner_id: int) -> dict[str, Any]:
        await self.stop_for_user(owner_id)
        return await self.start_for_user(owner_id)

    async def handle_guild_join(self, bot, guild: discord.Guild) -> None:
        owner_id = int(getattr(bot, "linked_owner_id", 0) or 0)
        allowed = set(getattr(bot, "linked_guild_ids", set()))
        if not owner_id or guild.id not in allowed:
            self.log.warning(
                "Linked bot for owner %s joined an unapproved guild %s; leaving immediately",
                owner_id,
                guild.id,
            )
            try:
                await guild.leave()
            except (discord.Forbidden, discord.HTTPException):
                self.log.exception("Unable to leave unapproved guild %s", guild.id)
            return
        try:
            target = discord.Object(id=guild.id)
            bot.tree.copy_global_to(guild=target)
            await bot.tree.sync(guild=target)
        except Exception:
            self.log.exception("Could not sync linked commands for allowed guild %s", guild.id)

    async def stop_all(self) -> None:
        for owner_id in list(self.instances):
            await self.stop_for_user(owner_id)
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
