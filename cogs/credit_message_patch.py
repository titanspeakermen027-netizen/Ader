"""Runtime formatting for ANORIS balance lookups."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class CreditMessagePatch(commands.Cog):
    """Replace balance-only /credits responses with the requested format."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._patched = False

    async def cog_load(self):
        economy = self.bot.get_cog("Economy")
        command = getattr(economy, "credits", None) if economy else None
        if command is None or not isinstance(command, app_commands.Command) or self._patched:
            return

        original = command.callback

        async def wrapped(cog_self, interaction: discord.Interaction, user=None, amount=None):
            if user is None and amount is None:
                balance = await cog_self.db.get_balance(interaction.user.id)
                return await interaction.response.send_message(
                    f"🪙  | **{interaction.user.display_name}, your ANORIS balance is `${balance:,}`.**",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            if user is not None and amount is None:
                balance = await cog_self.db.get_balance(user.id)
                return await interaction.response.send_message(
                    f"💳  | **{user.display_name} ANORIS account balance is `${balance:,}`.**",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return await original(cog_self, interaction, user, amount)

        command.callback = wrapped
        self._patched = True


async def setup(bot: commands.Bot):
    await bot.add_cog(CreditMessagePatch(bot))
