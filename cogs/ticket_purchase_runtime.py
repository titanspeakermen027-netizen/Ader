"""Runtime listener bridge for ticket-purchase transfer verification."""
from __future__ import annotations

import discord
from discord.ext import commands


class TicketPurchaseRuntime(commands.Cog):
    """Forward Discord message events to the TicketPurchase service."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        purchase = self.bot.get_cog("TicketPurchase")
        if purchase is None:
            return
        # TicketPurchase itself contains the complete verification logic;
        # this bridge only registers it as a real Discord event listener.
        await purchase.on_message(message)


async def setup(bot: commands.Bot):
    if bot.get_cog("TicketPurchase") is None:
        try:
            await bot.load_extension("cogs.ticket_purchase")
        except commands.ExtensionAlreadyLoaded:
            pass
    await bot.add_cog(TicketPurchaseRuntime(bot))
