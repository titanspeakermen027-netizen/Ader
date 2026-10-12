"""Owner-only bot profile editor using Discord Components V2."""
from __future__ import annotations

import io
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


PURPLE = 0x8B5CF6
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class BotEditTextModal(discord.ui.Modal):
    def __init__(self, cog: "BotEdit", mode: str):
        titles = {
            "name": "تغيير اسم ANO BOT",
            "avatar": "تغيير صورة ANO BOT",
            "banner": "تغيير بنر ANO BOT",
            "bio": "تغيير بايو ANO BOT",
        }
        super().__init__(title=titles[mode], timeout=180)
        self.cog = cog
        self.mode = mode
        if mode == "name":
            self.value = discord.ui.TextInput(
                label="الاسم الجديد",
                placeholder="ANO BOT",
                min_length=2,
                max_length=32,
                required=True,
            )
        elif mode in ("avatar", "banner"):
            self.value = discord.ui.TextInput(
                label="رابط الصورة المباشر",
                placeholder="https://example.com/image.png",
                style=discord.TextStyle.short,
                max_length=500,
                required=True,
            )
        else:
            self.value = discord.ui.TextInput(
                label="البايو الجديدة",
                placeholder="Your server. Your vision. ANO BOT.",
                style=discord.TextStyle.paragraph,
                max_length=190,
                required=True,
            )
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.cog.authorized(interaction.user):
            return await interaction.response.send_message(
                "❌ هاد اللوحة مخصصة لمالك البوت أو لشخص مخوّل باستعمال أوامر المالك.",
                ephemeral=True,
            )

        if self.mode == "name":
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                await self.cog.bot.user.edit(username=str(self.value.value).strip())
                return await interaction.followup.send("✅ تم تحديث اسم البوت.", ephemeral=True)
            except discord.HTTPException as exc:
                return await interaction.followup.send(
                    f"❌ تعذر تغيير الاسم. قد تكون هناك قيود مؤقتة من Discord. ({getattr(exc, 'status', 'HTTP error')})",
                    ephemeral=True,
                )

        if self.mode == "avatar":
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                image_bytes = await self.cog.download_image(str(self.value.value).strip())
                await self.cog.bot.user.edit(avatar=image_bytes)
                return await interaction.followup.send("✅ تم تحديث صورة البوت.", ephemeral=True)
            except ValueError as exc:
                return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            except discord.HTTPException:
                return await interaction.followup.send(
                    "❌ رفض Discord تحديث الصورة. جرّب صورة PNG أو JPG أو GIF بحجم أصغر.",
                    ephemeral=True,
                )

        if self.mode == "banner":
            return await interaction.response.send_message(
                "ℹ️ زر البانر موجود في اللوحة، لكن Discord لا يوفّر عبر واجهة Bot API العامة طريقة مدعومة لتغيير بانر حساب البوت. ما نقدرش نغيّرو بطريقة غير رسمية.",
                ephemeral=True,
            )

        return await interaction.response.send_message(
            "ℹ️ زر البايو موجود في اللوحة، لكن Discord لا يوفّر عبر واجهة Bot API العامة طريقة مدعومة لتعديل About Me لحساب البوت. ما نقدرش نغيّرو بطريقة غير رسمية.",
            ephemeral=True,
        )


class BotEditButton(discord.ui.Button):
    def __init__(self, cog: "BotEdit", mode: str, label: str, emoji: str, *, disabled: bool = False):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary if mode in ("name", "avatar") else discord.ButtonStyle.secondary,
            custom_id=f"ader:botedit:{mode}",
            disabled=disabled,
        )
        self.cog = cog
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        if not await self.cog.authorized(interaction.user):
            return await interaction.response.send_message(
                "❌ ما عندكش صلاحية استعمال إعدادات البوت.",
                ephemeral=True,
            )
        await interaction.response.send_modal(BotEditTextModal(self.cog, self.mode))


class BotEditView(discord.ui.LayoutView):
    def __init__(self, cog: "BotEdit", owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id

        container = discord.ui.Container(
            discord.ui.TextDisplay("# ✦ ANO BOT — Bot Editor"),
            discord.ui.TextDisplay(
                "تحكم في هوية البوت من لوحة واحدة. اختَر الخاصية التي تريد تعديلها.\n"
                "-# Powered by Ader Dashboard • Owner controls"
            ),
            discord.ui.Separator(),
            discord.ui.ActionRow(
                BotEditButton(cog, "name", "تغيير الاسم", "📝"),
                BotEditButton(cog, "avatar", "تغيير الصورة", "🖼️"),
            ),
            discord.ui.ActionRow(
                BotEditButton(cog, "banner", "تغيير البانر", "🌌"),
                BotEditButton(cog, "bio", "تغيير البايو", "✍️"),
            ),
            accent_colour=discord.Colour(PURPLE),
        )
        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.cog.authorized(interaction.user):
            return True
        await interaction.response.send_message(
            "❌ ما عندكش صلاحية استعمال هاد اللوحة.",
            ephemeral=True,
        )
        return False


class BotEdit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def authorized(self, user: discord.abc.User) -> bool:
        try:
            # Bot.is_owner also recognizes users granted the existing -بوت delegation.
            return bool(await self.bot.is_owner(user))
        except Exception:
            return False

    @staticmethod
    async def download_image(url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("دخل رابط صورة صحيح يبدأ بـ https:// أو http://.")
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    raise ValueError("ما قدرتش نحمّل الصورة من هاد الرابط.")
                content_type = response.headers.get("Content-Type", "").lower()
                if not content_type.startswith("image/"):
                    raise ValueError("الرابط خاصو يشير مباشرةً إلى ملف صورة.")
                data = await response.content.read(MAX_IMAGE_BYTES + 1)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("حجم الصورة كبير بزاف؛ الحد الأقصى هو 8 MB.")
                if not data:
                    raise ValueError("ملف الصورة فارغ.")
                return data

    @app_commands.command(
        name="botedit",
        description="تعديل البوت الاسم و الصورة و البانر و البايو [لمالك البوت فقط]",
    )
    async def botedit(self, interaction: discord.Interaction):
        if not await self.authorized(interaction.user):
            return await interaction.response.send_message(
                "❌ هذا الأمر مخصص لمالك البوت والأشخاص الذين منحهم صلاحيات المالك.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            view=BotEditView(self, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BotEdit(bot))
