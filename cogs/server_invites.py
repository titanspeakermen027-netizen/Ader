"""Owner-only command for listing Ader's guilds and creating an invite."""

from __future__ import annotations

import discord
from discord.ext import commands

_PAGE_SIZE = 25
_INVITE_MAX_AGE = 24 * 60 * 60


class ServerInviteView(discord.ui.View):
    """Private, paginated guild picker restricted to its original owner user."""

    def __init__(self, cog: "ServerInvites", author_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = int(author_id)
        self.guilds = self._sorted_guilds()
        self.page = 0
        self.message: discord.Message | None = None
        self._refresh_items()

    def _sorted_guilds(self) -> list[discord.Guild]:
        return sorted(
            self.cog.bot.guilds,
            key=lambda guild: (guild.name.casefold(), guild.id),
        )

    @property
    def page_count(self) -> int:
        return max(1, (len(self.guilds) + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def _page_guilds(self) -> list[discord.Guild]:
        start = self.page * _PAGE_SIZE
        return self.guilds[start:start + _PAGE_SIZE]

    def _embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="سيرفرات ANO BOT",
            description=(
                "اختار السيرفر من القائمة باش نوجد ليك رابط الدعوة ديالو.\n"
                "هاد القائمة خاصة بك وما كيباناش لباقي أعضاء السيرفر."
            ),
            colour=discord.Colour.dark_purple(),
        )
        embed.add_field(name="عدد السيرفرات", value=f"**{len(self.guilds):,}**", inline=True)
        embed.add_field(name="الصفحة", value=f"**{self.page + 1}/{self.page_count}**", inline=True)
        embed.set_footer(text="صلاحية رابط الدعوة: 24 ساعة")
        return embed

    def _refresh_items(self) -> None:
        self.clear_items()
        current_guilds = self._page_guilds()

        options = [
            discord.SelectOption(
                label=guild.name[:100],
                value=str(guild.id),
                description=f"Server ID: {guild.id}"[:100],
            )
            for guild in current_guilds
        ]

        if options:
            select = discord.ui.Select(
                placeholder="اختار السيرفر...",
                min_values=1,
                max_values=1,
                options=options,
                row=0,
            )

            async def select_callback(interaction: discord.Interaction) -> None:
                await self._select_guild(interaction, select.values[0])

            select.callback = select_callback
            self.add_item(select)

        if self.page_count > 1:
            previous = discord.ui.Button(
                label="السابق",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=self.page <= 0,
            )
            next_page = discord.ui.Button(
                label="التالي",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=self.page >= self.page_count - 1,
            )

            async def previous_callback(interaction: discord.Interaction) -> None:
                self.guilds = self._sorted_guilds()
                self.page = max(0, min(self.page - 1, self.page_count - 1))
                self._refresh_items()
                await interaction.response.edit_message(embed=self._embed(), view=self)

            async def next_callback(interaction: discord.Interaction) -> None:
                self.guilds = self._sorted_guilds()
                self.page = max(0, min(self.page + 1, self.page_count - 1))
                self._refresh_items()
                await interaction.response.edit_message(embed=self._embed(), view=self)

            previous.callback = previous_callback
            next_page.callback = next_callback
            self.add_item(previous)
            self.add_item(next_page)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "هذه القائمة مخصصة للشخص الذي طلبها فقط."
            )
            return False

        # Re-check owner/delegate status for every interaction, so a revoked
        # delegation cannot keep using an already-open menu.
        if not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "لم تعد تملك صلاحية استخدام أوامر صاحب البوت."
            )
            self.stop()
            return False

        return True

    async def _find_invite_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:
        bot_user = self.cog.bot.user
        if bot_user is None:
            return None

        bot_member = guild.me or guild.get_member(bot_user.id)
        if bot_member is None:
            return None

        channels = list(guild.text_channels)
        system_channel_id = guild.system_channel.id if guild.system_channel else None
        channels.sort(
            key=lambda channel: (
                channel.id != system_channel_id,
                channel.position,
                channel.id,
            )
        )

        for channel in channels:
            permissions = channel.permissions_for(bot_member)
            if permissions.view_channel and permissions.create_instant_invite:
                return channel

        return None

    async def _select_guild(
        self,
        interaction: discord.Interaction,
        guild_id_value: str,
    ) -> None:
        try:
            guild_id = int(guild_id_value)
        except (TypeError, ValueError):
            await interaction.response.send_message("اختيار السيرفر غير صالح.")
            return

        guild = self.cog.bot.get_guild(guild_id)
        if guild is None:
            await interaction.response.send_message(
                "هاد السيرفر ما بقاش ضمن السيرفرات اللي البوت داخل ليها. عاود استعمل الأمر."
            )
            self.stop()
            return

        await interaction.response.defer()

        channel = await self._find_invite_channel(guild)
        if channel is None:
            await interaction.followup.send(
                f"ما قدرتش نوجد دعوة لـ **{guild.name}**. خاص البوت تكون عندو صلاحيتا "
                "عرض القناة وإنشاء الدعوات في قناة نصية واحدة على الأقل."
            )
            return

        try:
            invite = await channel.create_invite(
                max_age=_INVITE_MAX_AGE,
                max_uses=0,
                unique=True,
                reason=(
                    f"ANO BOT server invite requested by "
                    f"{interaction.user} ({interaction.user.id})"
                ),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Discord رفض إنشاء الدعوة في **{guild.name}**. راجع صلاحيات البوت في القناة."
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                f"وقع خطأ من Discord أثناء إنشاء دعوة **{guild.name}**. حاول مرة أخرى."
            )
            return

        result = discord.Embed(
            title="تم إنشاء رابط الدعوة",
            description=f"تم إنشاء دعوة السيرفر **{guild.name}** بنجاح.",
            colour=discord.Colour.green(),
        )
        result.add_field(name="رابط الدعوة", value=f"[اضغط هنا للانضمام]({invite.url})", inline=False)
        result.add_field(name="القناة", value=channel.mention, inline=True)
        result.add_field(name="مدة الصلاحية", value="24 ساعة", inline=True)
        result.add_field(name="عدد الاستخدامات", value="غير محدود", inline=True)

        if interaction.message is not None:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                pass

        await interaction.followup.send(embed=result)
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ServerInvites(commands.Cog):
    """Owner/delegate command to browse all bot guilds and create an invite."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="سيرفرات")
    async def servers(self, ctx: commands.Context) -> None:
        # Uses the existing Bot.is_owner permission system, including users
        # granted the normal -بوت delegation. The separate !رست permission
        # is intentionally not sufficient.
        if not await self.bot.is_owner(ctx.author):
            await ctx.send(
                "هذا الأمر مخصص لصاحب البوت أو الأشخاص الذين مُنحوا صلاحيات أوامره.",
                delete_after=10,
            )
            return

        guilds = sorted(
            self.bot.guilds,
            key=lambda guild: (guild.name.casefold(), guild.id),
        )
        if not guilds:
            await ctx.send("البوت غير موجود حالياً في أي سيرفر.")
            return

        view = ServerInviteView(self, ctx.author.id)
        embed = view._embed()

        try:
            if ctx.guild is None:
                view.message = await ctx.send(embed=embed, view=view)
            else:
                view.message = await ctx.author.send(embed=embed, view=view)
                await ctx.send(
                    "تم إرسال قائمة السيرفرات إلى رسائلك الخاصة.",
                    delete_after=10,
                )
        except discord.Forbidden:
            await ctx.send(
                "ما قدرتش نرسل ليك القائمة في الخاص. فعّل استقبال الرسائل الخاصة من البوت، ثم عاود استعمل الأمر.",
                delete_after=15,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerInvites(bot))
