"""Owner-only user Premium grants for Ader."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands


_DURATION_RE = re.compile(r"^(\d+)(mo|y|w|d|h|m|s)$", re.I)
_USER_ID_RE = re.compile(r"^(?:<@!?(\d+)>|(\d+))$")


def parse_duration(value: str) -> int:
    match = _DURATION_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("invalid")
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount <= 0:
        raise ValueError("invalid")
    return {
        "y": amount * 365 * 86400,
        "mo": amount * 30 * 86400,
        "w": amount * 7 * 86400,
        "d": amount * 86400,
        "h": amount * 3600,
        "m": amount * 60,
        "s": amount,
    }[unit]


def fmt(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class ServerPremium(commands.Cog):
    """Keeps the old extension name, but Premium grants are now user-based."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def _allowed(self, user: discord.abc.User) -> bool:
        return bool(await self.bot.is_owner(user))

    async def _reply(self, message: discord.Message, title: str, description: str):
        embed = discord.Embed(title=title, description=description, colour=discord.Colour.dark_grey())
        await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    async def _target_id(self, message: discord.Message, raw: str) -> int:
        if message.mentions:
            if len(message.mentions) != 1:
                raise ValueError("حدد مستخدماً واحداً فقط.")
            return int(message.mentions[0].id)
        match = _USER_ID_RE.fullmatch(str(raw or "").strip())
        if not match:
            raise ValueError("أرسل منشن المستخدم أو Discord ID صحيحاً.")
        return int(match.group(1) or match.group(2))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id:
            return
        parts = message.content.strip().split()
        if not parts:
            return
        command = parts[0].lower().lstrip("!$")
        if command not in {"add-pre", "remove-pre"}:
            return

        if not await self._allowed(message.author):
            return await self._reply(message, "صلاحية مرفوضة", "هذا الأمر مخصص لمالك ANO BOT فقط.")

        if command == "add-pre":
            if len(parts) != 3:
                return await self._reply(
                    message,
                    "طريقة الاستخدام",
                    "add-pre @user/id 1mo\n"
                    "المدد المتاحة: 1y، 1mo، 7d، 12h، 30m.",
                )
            try:
                user_id = await self._target_id(message, parts[1])
                duration = parse_duration(parts[2])
            except ValueError as exc:
                return await self._reply(message, "بيانات غير صحيحة", str(exc))
            if user_id == message.author.id:
                return await self._reply(message, "عملية غير صالحة", "لا يمكن منح Premium لنفس حساب المالك عبر هذا الأمر.")
            try:
                target = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                return await self._reply(message, "المستخدم غير موجود", "تحقق من Discord ID وحاول مرة أخرى.")
            if target.bot:
                return await self._reply(message, "مستخدم غير صالح", "Premium مخصص لحسابات المستخدمين وليس حسابات البوتات.")

            now = time.time()
            previous = await self.db.get_user_premium(user_id)
            base = max(now, float(previous["expires_at"])) if previous else now
            expires_at = base + duration
            started_at = float(previous["started_at"]) if previous else now
            record = await self.db.set_user_premium(user_id, started_at, expires_at, message.author.id)
            return await self._reply(
                message,
                "تم تفعيل Ader Premium للمستخدم",
                f"المستخدم: {target.mention}\n"
                f"Discord ID: {user_id}\n"
                f"بداية الاشتراك: {fmt(record['started_at'])}\n"
                f"تاريخ الانتهاء: {fmt(record['expires_at'])}\n"
                "أصبح بإمكانه إعداد Linked Bot من الداشبورد واختيار السيرفرات التي يديرها.",
            )

        if len(parts) != 2:
            return await self._reply(message, "طريقة الاستخدام", "remove-pre @user/id")
        try:
            user_id = await self._target_id(message, parts[1])
        except ValueError as exc:
            return await self._reply(message, "بيانات غير صحيحة", str(exc))
        previous = await self.db.get_user_premium(user_id)
        if not previous:
            return await self._reply(message, "لا يوجد اشتراك", f"المستخدم {user_id} لا يملك اشتراك Premium فعالاً.")
        manager = getattr(self.bot, "linked_bot_manager", None)
        if manager is not None:
            await manager.stop_for_user(user_id)
        await self.db.remove_user_premium(user_id)
        return await self._reply(
            message,
            "تم إلغاء Premium",
            f"تم إلغاء Premium للمستخدم {user_id} وإيقاف Linked Bot الخاص به.",
        )


async def setup(bot):
    await bot.add_cog(ServerPremium(bot))
