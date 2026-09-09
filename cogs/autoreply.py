"""Per-server automatic replies for messages, including optional bot messages."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


MATCHING_TYPES = {
    "contains": "يحتوي على",
    "exact": "مطابقة كاملة",
    "starts_with": "يبدأ بـ",
    "ends_with": "ينتهي بـ",
}

REPLY_TYPES = {
    "reply": "رد على الرسالة",
    "message": "رسالة عادية",
}


class AutoReply(commands.Cog):
    """Configurable automatic responses stored in SQLite."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS autoreplies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                trigger TEXT NOT NULL,
                response TEXT NOT NULL,
                matching_type TEXT NOT NULL DEFAULT 'contains',
                reply_type TEXT NOT NULL DEFAULT 'reply',
                role_id INTEGER,
                case_sensitive INTEGER NOT NULL DEFAULT 0,
                reply_to_bots INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(guild_id, trigger, matching_type, case_sensitive)
            )"""
        )
        columns = await self.db.fetchall("PRAGMA table_info(autoreplies)")
        names = {str(row["name"]) for row in columns}
        migrations = {
            "reply_to_bots": "ALTER TABLE autoreplies ADD COLUMN reply_to_bots INTEGER NOT NULL DEFAULT 0",
            "enabled": "ALTER TABLE autoreplies ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
            "created_by": "ALTER TABLE autoreplies ADD COLUMN created_by INTEGER NOT NULL DEFAULT 0",
            "created_at": "ALTER TABLE autoreplies ADD COLUMN created_at REAL NOT NULL DEFAULT 0",
        }
        for name, sql in migrations.items():
            if name not in names:
                await self.db.execute(sql)

    @staticmethod
    def _normalize(text: str, case_sensitive: bool) -> str:
        value = str(text or "").strip()
        return value if case_sensitive else value.casefold()

    @classmethod
    def _matches(cls, content: str, trigger: str, matching_type: str, case_sensitive: bool) -> bool:
        content_n = cls._normalize(content, case_sensitive)
        trigger_n = cls._normalize(trigger, case_sensitive)
        if not trigger_n:
            return False
        if matching_type == "exact":
            return content_n == trigger_n
        if matching_type == "starts_with":
            return content_n.startswith(trigger_n)
        if matching_type == "ends_with":
            return content_n.endswith(trigger_n)
        return trigger_n in content_n

    @staticmethod
    def _has_role(member: discord.Member, role_id: int | None) -> bool:
        return role_id is None or any(role.id == role_id for role in getattr(member, "roles", ()))

    async def _send(self, message: discord.Message, row: Any) -> None:
        content = str(row["response"])
        if str(row["reply_type"]) == "reply":
            await message.reply(
                content,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await message.channel.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # The bot can answer another bot when explicitly enabled, but never itself.
        if self.bot.user and message.author.id == self.bot.user.id:
            return
        if message.guild is None or not message.content.strip():
            return
        rows = await self.db.fetchall(
            "SELECT * FROM autoreplies WHERE guild_id=? AND enabled=1 ORDER BY id ASC",
            (message.guild.id,),
        )
        author_is_bot = bool(message.author.bot)
        for row in rows:
            if author_is_bot and not bool(row["reply_to_bots"]):
                continue
            try:
                role_id = int(row["role_id"]) if row["role_id"] else None
                if isinstance(message.author, discord.Member) and not self._has_role(message.author, role_id):
                    continue
                if not self._matches(
                    message.content,
                    str(row["trigger"]),
                    str(row["matching_type"]),
                    bool(row["case_sensitive"]),
                ):
                    continue
                await self._send(message, row)
            except (discord.Forbidden, discord.HTTPException):
                continue
            except Exception as exc:
                logger = getattr(self.bot, "logger", None)
                if logger:
                    logger.error("AutoReply failed for rule %s: %s", row["id"], exc, exc_info=True)

    async def _can_manage(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and (permissions.administrator or permissions.manage_guild))

    autoreply = app_commands.Group(name="autoreply", description="إدارة الردود التلقائية")

    @autoreply.command(name="add", description="إضافة رد تلقائي جديد")
    @app_commands.describe(
        trigger="الكلمة أو العبارة التي ستفعّل الرد",
        response="الرد الذي سيرسله البوت",
        matching_type="نوع مطابقة الكلمة",
        reply_type="نوع الرد",
        role="رتبة مسموح لها بتفعيل الرد، اختياري",
        case_sensitive="هل المطابقة حساسة لحالة الأحرف؟",
        reply_to_bots="هل يرد البوت عندما تأتي الكلمة من بوت آخر؟",
    )
    @app_commands.choices(
        matching_type=[app_commands.Choice(name=label, value=value) for value, label in MATCHING_TYPES.items()],
        reply_type=[app_commands.Choice(name=label, value=value) for value, label in REPLY_TYPES.items()],
    )
    @app_commands.default_permissions(manage_guild=True)
    async def add(
        self,
        interaction: discord.Interaction,
        trigger: str,
        response: str,
        matching_type: app_commands.Choice[str] | None = None,
        reply_type: app_commands.Choice[str] | None = None,
        role: discord.Role | None = None,
        case_sensitive: bool = False,
        reply_to_bots: bool = False,
    ):
        if not await self._can_manage(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        trigger = trigger.strip()
        response = response.strip()
        matching = matching_type.value if matching_type else "contains"
        reply = reply_type.value if reply_type else "reply"
        if not trigger or not response:
            return await interaction.response.send_message("❌ يجب إدخال trigger و response.", ephemeral=True)
        if len(trigger) > 200:
            return await interaction.response.send_message("❌ الـtrigger طويل بزاف (الحد 200 حرف).", ephemeral=True)
        if len(response) > 4000:
            return await interaction.response.send_message("❌ الـresponse طويل بزاف (الحد 4000 حرف).", ephemeral=True)
        if matching not in MATCHING_TYPES or reply not in REPLY_TYPES:
            return await interaction.response.send_message("❌ إعدادات المطابقة غير صالحة.", ephemeral=True)
        try:
            await self.db.execute(
                """INSERT INTO autoreplies(
                    guild_id, trigger, response, matching_type, reply_type, role_id,
                    case_sensitive, reply_to_bots, enabled, created_by, created_at
                ) VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    interaction.guild.id,
                    trigger,
                    response,
                    matching,
                    reply,
                    role.id if role else None,
                    1 if case_sensitive else 0,
                    1 if reply_to_bots else 0,
                    interaction.user.id,
                    time.time(),
                ),
            )
        except Exception:
            return await interaction.response.send_message("❌ كاين Auto Reply بنفس الـtrigger والإعدادات ديالو مسبقاً.", ephemeral=True)

        bot_text = "✅ نعم" if reply_to_bots else "❌ لا"
        role_text = role.mention if role else "الجميع"
        embed = discord.Embed(title="✅ تم إضافة الرد التلقائي", colour=discord.Colour.green())
        embed.add_field(name="Trigger", value=f"`{trigger}`", inline=False)
        embed.add_field(name="Response", value=response, inline=False)
        embed.add_field(name="نوع المطابقة", value=MATCHING_TYPES[matching], inline=True)
        embed.add_field(name="نوع الرد", value=REPLY_TYPES[reply], inline=True)
        embed.add_field(name="الرتبة", value=role_text, inline=True)
        embed.add_field(name="case_sensitive", value="✅ نعم" if case_sensitive else "❌ لا", inline=True)
        embed.add_field(name="الرد على رسائل البوتات", value=bot_text, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autoreply.command(name="remove", description="حذف رد تلقائي")
    @app_commands.describe(id="ID ديال الرد التلقائي")
    @app_commands.default_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, id: int):
        if not await self._can_manage(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        cur = await self.db.execute("DELETE FROM autoreplies WHERE id=? AND guild_id=?", (id, interaction.guild.id))
        if cur.rowcount != 1:
            return await interaction.response.send_message("❌ ما لقيتش هاد الرد التلقائي.", ephemeral=True)
        await interaction.response.send_message("✅ تم حذف الرد التلقائي.", ephemeral=True)

    @autoreply.command(name="list", description="عرض الردود التلقائية")
    @app_commands.default_permissions(manage_guild=True)
    async def list_rules(self, interaction: discord.Interaction):
        if not await self._can_manage(interaction):
            return await interaction.response.send_message("❌ تحتاج إلى Manage Server أو Administrator.", ephemeral=True)
        rows = await self.db.fetchall(
            "SELECT id,trigger,response,matching_type,reply_type,role_id,case_sensitive,reply_to_bots,enabled FROM autoreplies WHERE guild_id=? ORDER BY id ASC",
            (interaction.guild.id,),
        )
        if not rows:
            return await interaction.response.send_message("ℹ️ ما كاين حتى رد تلقائي متسجل.", ephemeral=True)
        lines = []
        for row in rows[:25]:
            role_text = f"<@&{row['role_id']}>" if row["role_id"] else "الجميع"
            bots = "🤖" if row["reply_to_bots"] else ""
            lines.append(f"**#{row['id']}** `{row['trigger']}` → {row['response'][:80]} | {MATCHING_TYPES.get(row['matching_type'], row['matching_type'])} | {role_text} {bots}")
        embed = discord.Embed(title="🤖 الردود التلقائية", description="\n".join(lines), colour=discord.Colour.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReply(bot))
    if bot.get_cog("TicketPurchase") is None:
        try:
            await bot.load_extension("cogs.ticket_purchase")
        except commands.ExtensionAlreadyLoaded:
            pass
    if bot.get_cog("TicketPurchaseRuntime") is None:
        try:
            await bot.load_extension("cogs.ticket_purchase_runtime")
        except commands.ExtensionAlreadyLoaded:
            pass
