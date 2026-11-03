from __future__ import annotations

import json
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands


DEFAULT_CATEGORIES = {
    "messages": True,
    "members": True,
    "moderation": True,
    "channels": True,
    "roles": True,
}

CATEGORY_LABELS = {
    "messages": "الرسائل",
    "members": "الأعضاء",
    "moderation": "الإشراف",
    "channels": "القنوات",
    "roles": "الرتب",
}


class AuditLogs(commands.Cog):
    """Persistent Discord event logging shared with the ANO BOT dashboard."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS audit_log_settings (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                categories TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL DEFAULT 0
            )"""
        )

    async def get_settings(self, guild_id: int) -> dict[str, Any]:
        row = await self.bot.db.fetchone(
            "SELECT channel_id, categories FROM audit_log_settings WHERE guild_id=?",
            (int(guild_id),),
        )
        categories = dict(DEFAULT_CATEGORIES)
        channel_id = None
        if row:
            channel_id = row["channel_id"]
            try:
                saved = json.loads(row["categories"] or "{}")
                if isinstance(saved, dict):
                    for key in DEFAULT_CATEGORIES:
                        if key in saved:
                            categories[key] = bool(saved[key])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return {"channel_id": channel_id, "categories": categories}

    async def save_settings(
        self,
        guild_id: int,
        *,
        channel_id: Optional[int] = None,
        categories: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        current = await self.get_settings(guild_id)
        if channel_id is not None:
            current["channel_id"] = int(channel_id) if int(channel_id) > 0 else None
        if categories is not None:
            for key in DEFAULT_CATEGORIES:
                if key in categories:
                    current["categories"][key] = bool(categories[key])
        await self.bot.db.execute(
            """INSERT INTO audit_log_settings(guild_id, channel_id, categories, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                 channel_id=excluded.channel_id,
                 categories=excluded.categories,
                 updated_at=excluded.updated_at""",
            (
                int(guild_id),
                current["channel_id"],
                json.dumps(current["categories"], ensure_ascii=False),
                time.time(),
            ),
        )
        # Keep the legacy moderation logger pointed at the same destination.
        core = self.bot.get_cog("ProfessionalCore")
        if core is not None and hasattr(core, "settings") and hasattr(core, "save"):
            try:
                core_settings = await core.settings(int(guild_id))
                core_settings.setdefault("logs", {})["channel"] = current["channel_id"]
                await core.save(int(guild_id), core_settings)
            except Exception:
                pass
        return current

    async def _emit(
        self,
        guild: Optional[discord.Guild],
        category: str,
        event: str,
        title: str,
        description: str,
        data: Optional[dict[str, Any]] = None,
    ):
        if guild is None or category not in DEFAULT_CATEGORIES:
            return
        settings = await self.get_settings(guild.id)
        if not settings["categories"].get(category, True):
            return

        payload = {
            "category": category,
            "event": event,
            "title": title[:200],
            "description": description[:1200],
            **(data or {}),
        }
        try:
            await self.bot.db.record_analytics(guild.id, f"audit.{event}", payload)
        except Exception:
            pass

        channel_id = settings.get("channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=title[:256],
            description=description[:4000],
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"ANO BOT • سجل {CATEGORY_LABELS[category]}")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            return

    @app_commands.command(name="logs", description="تحديد قناة سجلات أحداث السيرفر")
    @app_commands.describe(channel="القناة التي ستصل إليها السجلات")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def logs(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "هذا الأمر متاح داخل السيرفر فقط.",
                ephemeral=True,
            )
        if channel is not None:
            await self.save_settings(interaction.guild.id, channel_id=channel.id)
            return await interaction.response.send_message(
                f"✅ تم تحديد قناة السجلات: {channel.mention}\n"
                "تقدر تعدّل أنواع الأحداث من لوحة تحكم ANO BOT.",
                ephemeral=True,
            )
        current = await self.get_settings(interaction.guild.id)
        channel_id = current.get("channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else "غير محددة"
        enabled = [
            CATEGORY_LABELS[key]
            for key, value in current["categories"].items()
            if value
        ]
        await interaction.response.send_message(
            f"📜 إعدادات السجلات\n"
            f"القناة: {channel_text}\n"
            f"الأقسام المفعّلة: {', '.join(enabled) if enabled else 'لا يوجد'}\n"
            "لتحديد قناة جديدة استعمل /logs channel:#القناة.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        message = payload.cached_message
        author = getattr(message, "author", None)
        if author is not None and getattr(author, "bot", False):
            return
        content = (getattr(message, "clean_content", "") or "").strip()
        preview = content[:500] if content else "محتوى الرسالة غير متاح في ذاكرة البوت."
        await self._emit(
            guild, "messages", "message_deleted", "🗑️ حذف رسالة",
            f"القناة: <#{payload.channel_id}>\n"
            f"العضو: {author.mention if author else 'غير معروف'}\n"
            f"معرّف الرسالة: {payload.message_id}\n"
            f"المحتوى: {preview}",
            {
                "channel_id": payload.channel_id,
                "message_id": payload.message_id,
                "user_id": getattr(author, "id", None),
                "content_preview": preview,
            },
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        message = payload.cached_message
        author = getattr(message, "author", None)
        if author is not None and getattr(author, "bot", False):
            return
        old_content = (getattr(message, "clean_content", "") or "").strip()
        new_content = str((payload.data or {}).get("content") or "").strip()
        if old_content == new_content or not new_content:
            return
        await self._emit(
            guild, "messages", "message_edited", "✏️ تعديل رسالة",
            f"القناة: <#{payload.channel_id}>\n"
            f"العضو: {author.mention if author else 'غير معروف'}\n"
            f"قبل: {(old_content or 'غير متاح')[:350]}\n"
            f"بعد: {new_content[:350]}",
            {
                "channel_id": payload.channel_id,
                "message_id": payload.message_id,
                "user_id": getattr(author, "id", None),
                "before": old_content[:500],
                "after": new_content[:500],
            },
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._emit(
            member.guild, "members", "member_joined", "📥 انضمام عضو",
            f"العضو: {member.mention}\nالاسم: {member}",
            {"user_id": member.id, "username": str(member)},
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._emit(
            member.guild, "members", "member_left", "📤 مغادرة عضو",
            f"العضو: {member}\nالمعرّف: {member.id}",
            {"user_id": member.id, "username": str(member)},
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._emit(
            guild, "moderation", "member_banned", "🔨 حظر عضو",
            f"العضو: {user}\nالمعرّف: {user.id}",
            {"user_id": user.id, "username": str(user)},
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await self._emit(
            guild, "moderation", "member_unbanned", "♻️ إلغاء حظر عضو",
            f"العضو: {user}\nالمعرّف: {user.id}",
            {"user_id": user.id, "username": str(user)},
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        name = getattr(channel, "name", "غير معروف")
        mention = getattr(channel, "mention", name)
        await self._emit(
            channel.guild, "channels", "channel_created", "➕ إنشاء قناة",
            f"القناة: {mention}\nالنوع: {channel.type}",
            {"channel_id": channel.id, "channel_name": name},
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._emit(
            channel.guild, "channels", "channel_deleted", "🗑️ حذف قناة",
            f"اسم القناة: {channel.name}\nالمعرّف: {channel.id}",
            {"channel_id": channel.id, "channel_name": channel.name},
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ):
        changes = []
        if before.name != after.name:
            changes.append(f"الاسم: {before.name} ← {after.name}")
        if getattr(before, "category_id", None) != getattr(after, "category_id", None):
            changes.append("تم تغيير تصنيف القناة.")
        if changes:
            await self._emit(
                after.guild, "channels", "channel_updated", "🛠️ تعديل قناة",
                f"القناة: {after.name}\n" + "\n".join(changes),
                {"channel_id": after.id, "changes": changes},
            )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._emit(
            role.guild, "roles", "role_created", "➕ إنشاء رتبة",
            f"الرتبة: {role.mention}\nالاسم: {role.name}",
            {"role_id": role.id, "role_name": role.name},
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._emit(
            role.guild, "roles", "role_deleted", "🗑️ حذف رتبة",
            f"اسم الرتبة: {role.name}\nالمعرّف: {role.id}",
            {"role_id": role.id, "role_name": role.name},
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(f"الاسم: {before.name} ← {after.name}")
        if before.permissions.value != after.permissions.value:
            changes.append("تم تعديل صلاحيات الرتبة.")
        if before.color != after.color:
            changes.append("تم تغيير لون الرتبة.")
        if changes:
            await self._emit(
                after.guild, "roles", "role_updated", "🛠️ تعديل رتبة",
                f"الرتبة: {after.mention}\n" + "\n".join(changes),
                {"role_id": after.id, "role_name": after.name, "changes": changes},
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AuditLogs(bot))
