"""Server Premium management for Ader."""
from __future__ import annotations
import re, time
from datetime import datetime, timezone
import discord
from discord.ext import commands

OWNER_ID = 1472570059367911587
_DURATION_RE = re.compile(r"^(\d+)(mo|y|w|d|h|m|s)$", re.I)

def parse_duration(value):
    match = _DURATION_RE.fullmatch((value or "").strip())
    if not match: raise ValueError("invalid")
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount <= 0: raise ValueError("invalid")
    seconds = {"y":amount*365*86400,"mo":amount*30*86400,"w":amount*7*86400,"d":amount*86400,"h":amount*3600,"m":amount*60,"s":amount}[unit]
    return seconds

def fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

class ServerPremium(commands.Cog):
    def __init__(self, bot):
        self.bot, self.db = bot, bot.db

    async def _allowed(self, user):
        return user.id == OWNER_ID or await self.bot.is_owner(user)

    async def _reply(self, message, title, description):
        embed = discord.Embed(title=title, description=description, colour=discord.Colour.dark_grey())
        await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    async def _set(self, message, duration, edit=False):
        if not await self._allowed(message.author):
            return await self._reply(message, "❌ صلاحية مرفوضة", "هذا الأمر مخصص لصاحب البوت أو من لديه صلاحية **بوت**.")
        try: seconds = parse_duration(duration)
        except ValueError:
            return await self._reply(message, "❌ مدة غير صحيحة", "مثال: `1y`, `1mo`, `7d`, `12h`, `30m`, `10s`.")
        now = time.time()
        old = await self.db.get_server_premium(message.guild.id)
        base = old["expires_at"] if edit and old and old["expires_at"] > now else now
        expires = base + seconds
        await self.db.set_server_premium(message.guild.id, message.guild.owner_id, now, expires, message.author.id)
        await self._reply(message, "تم تعديل البريميوم في سيرفر " + message.guild.name if edit else "تم تفعيل البريميوم في سيرفر " + message.guild.name,
            "**تاريخ التفعيل:**\n" + fmt(now) + "\n**تاريخ الانتهاء:**\n" + fmt(expires) + "\n**ايدي السيرفر:**\n`" + str(message.guild.id) + "`\n**ايدي صاحب السيرفر:**\n`" + str(message.guild.owner_id) + "`")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None: return
        parts = message.content.strip().split()
        if not parts: return
        cmd = parts[0].lower()
        if cmd not in {"prme","eprme","uprme"}: return
        if cmd == "uprme":
            if not await self._allowed(message.author):
                return await self._reply(message, "❌ صلاحية مرفوضة", "هذا الأمر مخصص لصاحب البوت أو من لديه صلاحية **بوت**.")
            await self.db.remove_server_premium(message.guild.id)
            return await self._reply(message, "تم إلغاء البريميوم من سيرفر " + message.guild.name, "**ايدي السيرفر:**\n`" + str(message.guild.id) + "`\n**ايدي صاحب السيرفر:**\n`" + str(message.guild.owner_id) + "`")
        if len(parts) != 2:
            return await self._reply(message, "❌ الاستعمال الصحيح", "`" + cmd + " 1mo`")
        await self._set(message, parts[1], cmd == "eprme")

async def setup(bot):
    await bot.add_cog(ServerPremium(bot))
