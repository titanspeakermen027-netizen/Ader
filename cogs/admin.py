"""
Admin Cog for Logiq
Administrative commands and bot management
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import logging
import sys

from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class Admin(commands.Cog):
    """Admin and management cog"""

    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    @app_commands.command(name="reload", description="إعادة تحميل إحدى وحدات البوت")
    @app_commands.describe(cog="اسم الوحدة المراد إعادة تحميلها")
    @is_admin()
    async def reload(self, interaction: discord.Interaction, cog: str):
        """Reload a cog"""
        try:
            await self.bot.reload_extension(f"cogs.{cog}")
            embed = EmbedFactory.success(
                "Cog Reloaded",
                f"Successfully reloaded **{cog}**"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"{interaction.user} reloaded cog {cog}")
        except commands.ExtensionNotLoaded:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", f"الوحدة **{cog}** غير محمّلة."),
                ephemeral=True
            )
        except commands.ExtensionNotFound:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", f"لم يتم العثور على الوحدة **{cog}**."),
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", f"تعذرت إعادة التحميل: {str(e)}"),
                ephemeral=True
            )
            logger.error(f"Error reloading cog {cog}: {e}", exc_info=True)

    @app_commands.command(name="sync", description="مزامنة أوامر الشرطة المائلة")
    @is_admin()
    async def sync(self, interaction: discord.Interaction):
        """Sync command tree"""
        await interaction.response.defer(ephemeral=True)

        try:
            synced = await self.bot.tree.sync()
            embed = EmbedFactory.success(
                "Commands Synced",
                f"Successfully synced **{len(synced)}** commands"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"{interaction.user} synced commands")
        except Exception as e:
            await interaction.followup.send(
                embed=EmbedFactory.error("خطأ", f"تعذرت المزامنة: {str(e)}"),
                ephemeral=True
            )
            logger.error(f"Error syncing commands: {e}", exc_info=True)

    @app_commands.command(name="modules", description="عرض الوحدات وتفعيلها أو تعطيلها")
    @is_admin()
    async def modules(self, interaction: discord.Interaction):
        """View module status"""
        modules = self.config.get('modules', {})

        description = ""
        for module_name, module_config in modules.items():
            enabled = module_config.get('enabled', True)
            status = "🟢 Enabled" if enabled else "🔴 Disabled"
            description += f"**{module_name.title()}**: {status}\n"

        embed = EmbedFactory.create(
            title="📦 وحدات البوت",
            description=description or "لم يتم إعداد أي وحدات.",
            color=EmbedColor.INFO
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="botinfo", description="عرض معلومات البوت")
    async def botinfo(self, interaction: discord.Interaction):
        """Display bot information"""
        # Calculate uptime
        uptime = discord.utils.utcnow() - self.bot.start_time if hasattr(self.bot, 'start_time') else None
        uptime_str = str(uptime).split('.')[0] if uptime else "Unknown"

        # Get stats
        total_guilds = len(self.bot.guilds)
        total_users = sum(g.member_count for g in self.bot.guilds)
        total_channels = sum(len(g.channels) for g in self.bot.guilds)

        embed = EmbedFactory.create(
            title="🤖 معلومات Ader",
            color=EmbedColor.PRIMARY,
            thumbnail=self.bot.user.display_avatar.url if self.bot.user else None,
            fields=[
                {"name": "📊 Servers", "value": str(total_guilds), "inline": True},
                {"name": "👥 Users", "value": f"{total_users:,}", "inline": True},
                {"name": "📺 Channels", "value": str(total_channels), "inline": True},
                {"name": "⏰ Uptime", "value": uptime_str, "inline": True},
                {"name": "🐍 Python Version", "value": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "inline": True},
                {"name": "📚 Discord.py", "value": discord.__version__, "inline": True},
                {"name": "💾 Database", "value": "MongoDB (Motor)", "inline": True},
                {"name": "🔗 Latency", "value": f"{round(self.bot.latency * 1000)}ms", "inline": True}
            ]
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setlogchannel", description="تعيين قناة السجلات")
    @app_commands.describe(channel="القناة المخصصة لسجلات الإشراف")
    @is_admin()
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set log channel"""
        guild_config = await self.db.get_guild(interaction.guild.id)
        if not guild_config:
            guild_config = await self.db.create_guild(interaction.guild.id)

        await self.db.update_guild(interaction.guild.id, {'log_channel': channel.id})

        embed = EmbedFactory.success(
            "Log Channel Set",
            f"Moderation logs will be sent to {channel.mention}"
        )
        await interaction.response.send_message(embed=embed)
        logger.info(f"Log channel set to {channel} in {interaction.guild}")

    @app_commands.command(name="config", description="عرض إعدادات الخادم")
    @is_admin()
    async def config(self, interaction: discord.Interaction):
        """View server configuration"""
        guild_config = await self.db.get_guild(interaction.guild.id)

        if not guild_config:
            await interaction.response.send_message(
                embed=EmbedFactory.info("لا توجد إعدادات", "لم يتم إعداد هذا الخادم بعد."),
                ephemeral=True
            )
            return

        log_channel = f"<#{guild_config.get('log_channel')}>" if guild_config.get('log_channel') else "Not set"
        welcome_channel = f"<#{guild_config.get('welcome_channel')}>" if guild_config.get('welcome_channel') else "Not set"
        verified_role = f"<@&{guild_config.get('verified_role')}>" if guild_config.get('verified_role') else "Not set"

        embed = EmbedFactory.create(
            title="⚙️ إعدادات الخادم",
            color=EmbedColor.INFO,
            fields=[
                {"name": "Log Channel", "value": log_channel, "inline": False},
                {"name": "Welcome Channel", "value": welcome_channel, "inline": False},
                {"name": "Verified Role", "value": verified_role, "inline": False},
                {"name": "Verification Type", "value": guild_config.get('verification_type', 'button'), "inline": True}
            ]
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="purge", description="حذف مجموعة من الرسائل")
    @app_commands.describe(amount="عدد الرسائل المراد حذفها (1–100)")
    @is_admin()
    async def purge(self, interaction: discord.Interaction, amount: int):
        """Purge messages"""
        if amount < 1 or amount > 100:
            await interaction.response.send_message(
                embed=EmbedFactory.error("عدد غير صالح", "يجب أن يكون العدد بين 1 و100."),
                ephemeral=True
            )
            return

        try:
            deleted = await interaction.channel.purge(limit=amount)
            embed = EmbedFactory.success(
                "Messages Purged",
                f"Deleted **{len(deleted)}** messages"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True, delete_after=5)
            logger.info(f"{interaction.user} purged {len(deleted)} messages in {interaction.channel}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", "لا يملك البوت صلاحية حذف الرسائل."),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Setup function for cog loading"""
    await bot.add_cog(Admin(bot, bot.db, bot.config))
