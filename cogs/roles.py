"""
Roles Cog for Logiq
Self-assignable roles with modal-based setup
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, List
import logging

from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin
from database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class RoleMenuSetupModal(discord.ui.Modal, title="إنشاء قائمة رتب"):
    """Modal for creating role menus with custom settings"""

    title_input = discord.ui.TextInput(
        label="عنوان القائمة",
        placeholder="مثال: اختر الرتب المناسبة",
        required=True,
        max_length=100
    )

    description_input = discord.ui.TextInput(
        label="وصف القائمة",
        placeholder="مثال: اختر الرتب المناسبة لك من القائمة أدناه",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    role_mentions = discord.ui.TextInput(
        label="الرتب (اذكرها باستخدام @)",
        placeholder="اكتب @ واختر الرتب. مثال: @لاعب @فنان @مطور",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    exclusive = discord.ui.TextInput(
        label="رتبة حصرية؟ (نعم/لا)",
        placeholder="اكتب نعم إذا كان بإمكان المستخدم اختيار رتبة واحدة فقط",
        required=True,
        max_length=3
    )

    def __init__(self, cog, channel):
        super().__init__()
        self.cog = cog
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission"""
        import re
        
        # Parse exclusive setting
        is_exclusive = self.exclusive.value.strip().lower() in ['yes', 'y', 'true', 'نعم', 'أجل', 'اجل', 'صحيح']

        # Parse role mentions
        role_list = []
        text = self.role_mentions.value
        role_ids = re.findall(r'<@&(\d+)>', text)

        if not role_ids:
            await interaction.response.send_message(
                embed=EmbedFactory.error("لم يتم العثور على رتب", "يرجى ذكر الرتب باستخدام @، ثم اختيارها من القائمة التي تظهر."),
                ephemeral=True
            )
            return

        for role_id in role_ids:
            role = interaction.guild.get_role(int(role_id))
            if role:
                # Skip @everyone and bot integration roles
                if role.is_default() or role.is_integration():
                    continue
                    
                role_emoji = None
                if role.unicode_emoji:
                    role_emoji = role.unicode_emoji
                elif role.icon:
                    role_emoji = str(role.icon)

                role_list.append({
                    'role': role,
                    'emoji': role_emoji or "🎭",
                    'label': role.name
                })

        if not role_list:
            await interaction.response.send_message(
                embed=EmbedFactory.error("لا توجد رتب صالحة", f"تم العثور على {len(role_ids)} إشارة إلى رتبة، لكن لا يمكن استخدامها؛ فقد تكون رتبة بوت أو رتبة @everyone."),
                ephemeral=True
            )
            return

        if len(role_list) > 25:
            await interaction.response.send_message(
                embed=EmbedFactory.error("عدد الرتب كبير جدًا", "يسمح Discord بحد أقصى 25 خيارًا في كل قائمة."),
                ephemeral=True
            )
            return

        # Create embed
        embed = EmbedFactory.create(
            title=self.title_input.value,
            description=self.description_input.value or "اختر الرتب من القائمة المنسدلة أدناه.",
            color=EmbedColor.PRIMARY
        )

        # Add field showing available roles
        roles_text = "\n".join([f"{r['emoji']} {r['role'].mention}" for r in role_list])
        embed.add_field(
            name="الرتب المتاحة",
            value=roles_text,
            inline=False
        )

        # Create view
        if is_exclusive:
            view = ExclusiveRoleView(role_list, self.title_input.value)
        else:
            view = MultiRoleView(role_list)

        # Send to channel
        await self.channel.send(embed=embed, view=view)

        # Respond to interaction
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                "تم إنشاء قائمة الرتب!",
                f"تم إنشاء قائمة {'حصرية' if is_exclusive else 'متعددة الاختيارات'} في {self.channel.mention}"
            ),
            ephemeral=True
        )

        logger.info(f"Role menu created by {interaction.user} with {len(role_list)} roles")


class ExclusiveRoleSelect(discord.ui.Select):
    """Dropdown for exclusive role selection (pick only one)"""

    def __init__(self, role_data: List[dict], category_name: str):
        options = [
            discord.SelectOption(
                label=r['label'],
                description=f"احصل على رتبة {r['label']}",
                value=str(r['role'].id),
                emoji=r['emoji']
            )
            for r in role_data[:25]
        ]

        super().__init__(
            placeholder="اختر أحد الخيارات...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"exclusive_role_{category_name[:50]}"
        )
        self.role_ids = [r['role'].id for r in role_data]

    async def callback(self, interaction: discord.Interaction):
        """Handle exclusive role selection - LOCKED after first selection"""
        try:
            # Check if user already has any role from THIS MENU ONLY
            user_has_role = False
            existing_role = None
            for role_id in self.role_ids:
                role = interaction.guild.get_role(role_id)
                if role and role in interaction.user.roles:
                    user_has_role = True
                    existing_role = role
                    break

            if user_has_role:
                await interaction.response.send_message(
                    embed=EmbedFactory.error(
                        "🔒 تم اختيار رتبة بالفعل",
                        f"لديك بالفعل رتبة **{existing_role.name}**. لا يمكنك اختيار رتبة أخرى من هذه القائمة."
                    ),
                    ephemeral=True
                )
                return

            selected_role_id = int(self.values[0])
            selected_role = interaction.guild.get_role(selected_role_id)

            if not selected_role:
                await interaction.response.send_message(
                    embed=EmbedFactory.error("خطأ", "لم يتم العثور على الرتبة"),
                    ephemeral=True
                )
                return

            # Give the selected role (only this one, no removing others)
            await interaction.user.add_roles(selected_role, reason="Exclusive role menu selection")

            embed = EmbedFactory.success(
                "<:success:1560285005018566757> تم اختيار الرتبة!",
                f"أصبحت تملك رتبة **{selected_role.name}** الآن!\n\n"
                f"**ملاحظة:** لا يمكنك اختيار رتبة أخرى من هذه القائمة."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            logger.info(f"{interaction.user} selected exclusive role {selected_role.name}")

        except discord.Forbidden:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", "لا يملك البوت صلاحية إدارة رتبك. يرجى التواصل مع أحد المسؤولين."),
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in role selection: {e}", exc_info=True)
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", f"تعذر إسناد الرتبة: {str(e)}"),
                ephemeral=True
            )


class MultiRoleSelect(discord.ui.Select):
    """Dropdown menu for multiple role selection"""

    def __init__(self, role_data: List[dict]):
        options = [
            discord.SelectOption(
                label=r['label'],
                description=f"تبديل حالة رتبة {r['label']}",
                value=str(r['role'].id),
                emoji=r['emoji']
            )
            for r in role_data[:25]
        ]

        super().__init__(
            placeholder="اختر الرتب المراد إضافتها أو إزالتها...",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id="multi_role_select"
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle role selection"""
        try:
            selected_role_ids = {int(value) for value in self.values}
            current_role_ids = {role.id for role in interaction.user.roles}

            roles_to_add = []
            roles_to_remove = []

            available_role_ids = {int(option.value) for option in self.options}

            for role_id in available_role_ids:
                role = interaction.guild.get_role(role_id)
                if not role:
                    continue

                if role_id in selected_role_ids and role_id not in current_role_ids:
                    roles_to_add.append(role)
                elif role_id not in selected_role_ids and role_id in current_role_ids:
                    roles_to_remove.append(role)

            if roles_to_add:
                await interaction.user.add_roles(*roles_to_add, reason="Role menu selection")
            if roles_to_remove:
                await interaction.user.remove_roles(*roles_to_remove, reason="Role menu deselection")

            changes = []
            if roles_to_add:
                changes.append(f"**تمت الإضافة:** {', '.join([r.name for r in roles_to_add])}")
            if roles_to_remove:
                changes.append(f"**تمت الإزالة:** {', '.join([r.name for r in roles_to_remove])}")

            if not changes:
                changes.append("لم يتم إجراء أي تغييرات.")

            embed = EmbedFactory.success(
                "<:success:1560285005018566757> تم تحديث الرتب!",
                "\n".join(changes)
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except discord.Forbidden:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", "لا يملك البوت صلاحية إدارة رتبك. يرجى التواصل مع أحد المسؤولين."),
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in multi-role selection: {e}", exc_info=True)
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", f"تعذر تحديث الرتب: {str(e)}"),
                ephemeral=True
            )


class ExclusiveRoleView(discord.ui.View):
    """View for exclusive role selection"""

    def __init__(self, role_data: List[dict], category_name: str):
        super().__init__(timeout=None)
        self.add_item(ExclusiveRoleSelect(role_data, category_name))


class MultiRoleView(discord.ui.View):
    """View for multi role selection"""

    def __init__(self, role_data: List[dict]):
        super().__init__(timeout=None)
        self.add_item(MultiRoleSelect(role_data))


class Roles(commands.Cog):
    """Role management cog"""

    def __init__(self, bot: commands.Bot, db: DatabaseManager, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get('modules', {}).get('roles', {})
    async def _register_persistent_views(self):
        """Register persistent views for role menus"""
        await self.bot.wait_until_ready()
        # Views are automatically re-registered when messages are loaded
        logger.info("Role menu persistent views ready")

    @app_commands.command(name="create-role-menu", description="إنشاء قائمة رتب (للمسؤولين)")
    @app_commands.describe(
        title="عنوان قائمة الرتب",
        description="وصف قائمة الرتب",
        role1="الرتبة الأولى",
        role2="الرتبة الثانية (اختيارية)",
        role3="الرتبة الثالثة (اختيارية)",
        role4="الرتبة الرابعة (اختيارية)",
        role5="الرتبة الخامسة (اختيارية)",
        role6="الرتبة السادسة (اختيارية)",
        role7="الرتبة السابعة (اختيارية)",
        role8="الرتبة الثامنة (اختيارية)",
        role9="الرتبة التاسعة (اختيارية)",
        role10="الرتبة العاشرة (اختيارية)",
        exclusive="هل يمكن للمستخدم اختيار رتبة واحدة فقط؟ (نعم/لا)",
        channel="القناة التي ستُرسل فيها القائمة (اختيارية)"
    )
    @is_admin()
    async def create_role_menu(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        role1: discord.Role,
        exclusive: str,
        role2: Optional[discord.Role] = None,
        role3: Optional[discord.Role] = None,
        role4: Optional[discord.Role] = None,
        role5: Optional[discord.Role] = None,
        role6: Optional[discord.Role] = None,
        role7: Optional[discord.Role] = None,
        role8: Optional[discord.Role] = None,
        role9: Optional[discord.Role] = None,
        role10: Optional[discord.Role] = None,
        channel: Optional[discord.TextChannel] = None
    ):
        """Create role menu directly with slash command"""
        target_channel = channel or interaction.channel
        is_exclusive = exclusive.strip().lower() in ['yes', 'y', 'true', 'نعم', 'أجل', 'اجل', 'صحيح']
        
        # Collect all roles
        roles = [role1]
        if role2:
            roles.append(role2)
        if role3:
            roles.append(role3)
        if role4:
            roles.append(role4)
        if role5:
            roles.append(role5)
        if role6:
            roles.append(role6)
        if role7:
            roles.append(role7)
        if role8:
            roles.append(role8)
        if role9:
            roles.append(role9)
        if role10:
            roles.append(role10)
        
        # Build role list
        role_list = []
        for role in roles:
            if role.is_default() or role.is_integration():
                continue
            
            role_emoji = None
            if role.unicode_emoji:
                role_emoji = role.unicode_emoji
            elif role.icon:
                role_emoji = str(role.icon)
            
            role_list.append({
                'role': role,
                'emoji': role_emoji or "🎭",
                'label': role.name
            })
        
        if not role_list:
            await interaction.response.send_message(
                embed=EmbedFactory.error("لا توجد رتب صالحة", "يرجى اختيار رتب صالحة."),
                ephemeral=True
            )
            return
        
        # Create embed
        embed = EmbedFactory.create(
            title=title,
            description=description,
            color=EmbedColor.PRIMARY
        )
        
        # Add field showing available roles
        roles_text = "\n".join([f"{r['emoji']} {r['role'].mention}" for r in role_list])
        embed.add_field(
            name="الرتب المتاحة",
            value=roles_text,
            inline=False
        )
        
        # Create view
        if is_exclusive:
            view = ExclusiveRoleView(role_list, title)
        else:
            view = MultiRoleView(role_list)
        
        # Send to channel
        await target_channel.send(embed=embed, view=view)
        
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                "تم إنشاء قائمة الرتب!",
                f"تم إنشاء قائمة {'حصرية' if is_exclusive else 'متعددة الاختيارات'} في {target_channel.mention}"
            ),
            ephemeral=True
        )
        
        logger.info(f"Role menu created by {interaction.user} with {len(role_list)} roles")

    @app_commands.command(name="addrole", description="إسناد رتبة إلى عضو (للمسؤولين)")
    @app_commands.describe(user="العضو المراد إسناد الرتبة إليه", role="الرتبة المراد إسنادها")
    @is_admin()
    async def add_role(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        """Add role to user"""
        if role in user.roles:
            await interaction.response.send_message(
                embed=EmbedFactory.info("الرتبة موجودة بالفعل", f"يمتلك {user.mention} الرتبة {role.mention} بالفعل."),
                ephemeral=True
            )
            return

        try:
            await user.add_roles(role)
            embed = EmbedFactory.success("تم إسناد الرتبة", f"تم إسناد {role.mention} إلى {user.mention}.")
            await interaction.response.send_message(embed=embed)
            logger.info(f"{interaction.user} added role {role} to {user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", "لا يملك البوت صلاحية إدارة الرتب."),
                ephemeral=True
            )

    @app_commands.command(name="removerole", description="إزالة رتبة من عضو (للمسؤولين)")
    @app_commands.describe(user="العضو المراد إزالة الرتبة منه", role="الرتبة المراد إزالتها")
    @is_admin()
    async def remove_role(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        """Remove role from user"""
        if role not in user.roles:
            await interaction.response.send_message(
                embed=EmbedFactory.info("الرتبة غير موجودة لدى العضو", f"لا يمتلك {user.mention} الرتبة {role.mention}."),
                ephemeral=True
            )
            return

        try:
            await user.remove_roles(role)
            embed = EmbedFactory.success("تمت إزالة الرتبة", f"تمت إزالة {role.mention} من {user.mention}.")
            await interaction.response.send_message(embed=embed)
            logger.info(f"{interaction.user} removed role {role} from {user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=EmbedFactory.error("خطأ", "لا يملك البوت صلاحية إدارة الرتب."),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Setup function for cog loading"""
    await bot.add_cog(Roles(bot, bot.db, bot.config))
