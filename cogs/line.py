"""Role-restricted image trigger for Ader."""
from __future__ import annotations

import io
import logging
import mimetypes
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("Ader.line")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".tif", ".tiff"}


class Line(commands.Cog):
    """Send a saved image when an authorized member writes its trigger."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS line_configs (
                guild_id INTEGER PRIMARY KEY,
                keyword TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                image_data BLOB NOT NULL,
                image_filename TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    @staticmethod
    def _can_manage(interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and (permissions.administrator or permissions.manage_guild))

    @app_commands.command(
        name="line",
        description="إعداد صورة تُرسل تلقائياً عند كتابة كلمة محددة",
    )
    @app_commands.describe(
        image="الصورة التي سيرسلها البوت",
        keyword="الكلمة أو العبارة التي ستفعّل إرسال الصورة",
        role="الرتبة المسموح لها فقط بتفعيل الصورة",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def line(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        keyword: str,
        role: discord.Role,
    ) -> None:
        """Create or replace this server's image trigger."""
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True
            )

        if not self._can_manage(interaction):
            return await interaction.response.send_message(
                "❌ تحتاج إلى صلاحية Manage Server أو Administrator لإعداد /line.",
                ephemeral=True,
            )

        keyword = keyword.strip()
        if not keyword:
            return await interaction.response.send_message(
                "❌ خاصك تحدد الكلمة أو العبارة اللي غادي تفعّل الصورة.",
                ephemeral=True,
            )
        if len(keyword) > 100:
            return await interaction.response.send_message(
                "❌ الكلمة طويلة بزاف؛ الحد الأقصى هو 100 حرف.",
                ephemeral=True,
            )
        if role.is_default():
            return await interaction.response.send_message(
                "❌ ما يمكنش تختار رتبة @everyone. اختار رتبة محددة.",
                ephemeral=True,
            )
        if image.size > MAX_IMAGE_BYTES:
            return await interaction.response.send_message(
                "❌ حجم الصورة كبير بزاف. الحد الأقصى هو 25 MB.",
                ephemeral=True,
            )

        content_type = image.content_type or mimetypes.guess_type(image.filename)[0] or ""
        if not content_type.lower().startswith("image/") and Path(image.filename).suffix.lower() not in IMAGE_EXTENSIONS:
            return await interaction.response.send_message(
                "❌ الملف خاصو يكون صورة بصيغة معروفة بحال PNG أو JPG أو GIF أو WEBP.",
                ephemeral=True,
            )

        bot_member = guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            return await interaction.response.send_message(
                "❌ ما قدرتش نتحقق من صلاحيات البوت في هذا السيرفر.",
                ephemeral=True,
            )
        bot_permissions = bot_member.guild_permissions
        missing = []
        if not bot_permissions.manage_messages:
            missing.append("Manage Messages")
        if not bot_permissions.send_messages:
            missing.append("Send Messages")
        if not bot_permissions.attach_files:
            missing.append("Attach Files")
        if missing:
            return await interaction.response.send_message(
                "❌ البوت خاصو الصلاحيات التالية باش يخدم /line: "
                + ", ".join(missing)
                + ".",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            image_data = await image.read()
            if not image_data:
                return await interaction.followup.send(
                    "❌ ما قدرتش نقرا الصورة المرفوعة. جرّب ترفعها من جديد.",
                    ephemeral=True,
                )
            if len(image_data) > MAX_IMAGE_BYTES:
                return await interaction.followup.send(
                    "❌ حجم الصورة كبير بزاف. الحد الأقصى هو 25 MB.",
                    ephemeral=True,
                )

            filename = Path(image.filename).name[:100] or "line-image.png"
            await self.db.execute(
                """
                INSERT INTO line_configs(
                    guild_id, keyword, role_id, image_data, image_filename, updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    keyword=excluded.keyword,
                    role_id=excluded.role_id,
                    image_data=excluded.image_data,
                    image_filename=excluded.image_filename,
                    updated_at=excluded.updated_at
                """,
                (guild.id, keyword, role.id, image_data, filename, time.time()),
            )
        except (discord.HTTPException, OSError, ValueError) as exc:
            logger.warning("Could not save /line image for guild %s: %s", guild.id, exc)
            return await interaction.followup.send(
                "❌ وقع مشكل أثناء حفظ الصورة. جرّب صورة أخرى.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("Failed to store /line config for guild %s", guild.id)
            return await interaction.followup.send(
                "❌ ما قدرش البوت يحفظ إعدادات /line بسبب خطأ داخلي.",
                ephemeral=True,
            )

        await interaction.followup.send(
            f"✅ تفعّل /line بنجاح.\n"
            f"**الكلمة:** `{keyword}`\n"
            f"**الرتبة المسموح لها:** {role.mention}\n"
            "أي عضو عنده هاد الرتبة وكتب الكلمة، البوت غادي يحذف رسالته ويرسل الصورة. "
            "اللي ما عندوش الرتبة، ما غادي يوقع ليه والو.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.content.strip():
            return

        member = message.author
        if not isinstance(member, discord.Member):
            member = message.guild.get_member(message.author.id)
        if member is None:
            return

        try:
            row = await self.db.fetchone(
                "SELECT keyword, role_id, image_data, image_filename "
                "FROM line_configs WHERE guild_id=?",
                (message.guild.id,),
            )
        except Exception:
            logger.exception("Could not read /line config for guild %s", message.guild.id)
            return

        if not row:
            return
        keyword = str(row["keyword"] or "").strip()
        if not keyword or message.content.strip().casefold() != keyword.casefold():
            return

        try:
            allowed_role_id = int(row["role_id"])
        except (TypeError, ValueError):
            return
        if not any(role.id == allowed_role_id for role in member.roles):
            # Unauthorized users are ignored completely; their messages stay untouched.
            return

        bot_member = message.guild.me
        if bot_member is None and self.bot.user is not None:
            bot_member = message.guild.get_member(self.bot.user.id)
        if bot_member is None:
            return

        channel_permissions = message.channel.permissions_for(bot_member)
        if not (
            channel_permissions.manage_messages
            and channel_permissions.send_messages
            and channel_permissions.attach_files
        ):
            # Do not delete anything when the bot cannot complete the full action.
            return

        image_data = bytes(row["image_data"] or b"")
        if not image_data:
            return
        filename = Path(str(row["image_filename"] or "line-image.png")).name[:100]
        if not filename:
            filename = "line-image.png"

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            logger.warning(
                "Could not delete /line trigger message in guild %s: %s",
                message.guild.id,
                exc,
            )
            return

        try:
            await message.channel.send(
                file=discord.File(io.BytesIO(image_data), filename=filename),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Could not send saved /line image in guild %s", message.guild.id)


async def setup(bot: commands.Bot) -> None:
    """Register the /line cog."""
    await bot.add_cog(Line(bot))
