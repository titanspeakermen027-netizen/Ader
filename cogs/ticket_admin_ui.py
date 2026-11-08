"""Native Discord controls for configuring Ader's ticket system.

The dashboard and this UI write to the same ticket_settings and ticket_panels
records, so either interface can be used without keeping separate configs.
"""
from __future__ import annotations

import json
from typing import Any

import discord


SETTING_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "default_category_id": None,
    "default_support_role_id": None,
    "default_support_role_ids": [],
    "transcript_channel_id": None,
    "log_channel_id": None,
    "claim_enabled": True,
    "rating_enabled": True,
    "allow_user_close": True,
    "allow_user_reopen": False,
    "allow_member_add": True,
    "allow_member_remove": True,
    "allow_rename": True,
    "allow_lock": True,
    "keep_closed": True,
    "delete_after_close_seconds": 0,
    "max_open_per_user": 1,
    "channel_name_template": "ticket-{number}-{user}",
}

TOGGLE_LABELS = {
    "enabled": "تشغيل نظام التذاكر",
    "claim_enabled": "السماح باستلام التذاكر",
    "rating_enabled": "تفعيل تقييم التذاكر",
    "allow_user_close": "السماح لصاحب التذكرة بالإغلاق",
    "allow_user_reopen": "السماح لصاحب التذكرة بإعادة الفتح",
    "allow_member_add": "السماح بإضافة أعضاء",
    "allow_member_remove": "السماح بإزالة أعضاء",
    "allow_rename": "السماح بإعادة التسمية",
    "keep_closed": "الاحتفاظ بالتذاكر المغلقة",
}


def _as_id(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    text = str(value).strip()
    for prefix in ("<#", "<@&", "<@!"):
        if text.startswith(prefix) and text.endswith(">"):
            text = text[len(prefix):-1]
            break
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_url(value: Any) -> str | None:
    value = str(value or "").strip()
    return value[:1000] if value.startswith(("https://", "http://")) else None


def _safe_color(value: Any, fallback: str = "#5865F2") -> str:
    value = str(value or "").strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.upper()
        except ValueError:
            pass
    return fallback


def _settings(value: Any) -> dict[str, Any]:
    result = dict(SETTING_DEFAULTS)
    if isinstance(value, dict):
        result.update(value)
    try:
        result["max_open_per_user"] = max(1, min(10, int(result.get("max_open_per_user", 1))))
    except (TypeError, ValueError):
        result["max_open_per_user"] = 1
    try:
        result["delete_after_close_seconds"] = max(0, min(3600, int(result.get("delete_after_close_seconds", 0))))
    except (TypeError, ValueError):
        result["delete_after_close_seconds"] = 0
    return result


def _can_manage(interaction: discord.Interaction, owner_id: int) -> bool:
    if interaction.user.id != owner_id or not isinstance(interaction.user, discord.Member):
        return False
    permissions = interaction.user.guild_permissions
    return bool(permissions.administrator or permissions.manage_guild)


def _settings_embed(guild: discord.Guild, value: Any) -> discord.Embed:
    config = _settings(value)

    def channel_text(key: str) -> str:
        channel_id = _as_id(config.get(key))
        channel = guild.get_channel(channel_id or 0)
        return channel.mention if channel else "غير محددة"

    role_ids = config.get("default_support_role_ids") or ([config.get("default_support_role_id")] if config.get("default_support_role_id") else [])
    roles = [guild.get_role(int(value)) for value in role_ids if _as_id(value)]
    role_text = "، ".join(role.mention for role in roles if role) or "غير محددة"
    embed = discord.Embed(
        title="إعدادات نظام التذاكر",
        description=(
            f"حالة النظام: **{'مفعّل' if config['enabled'] else 'معطّل'}**\n"
            f"فئة التذاكر: **{channel_text('default_category_id')}**\n"
            f"رتبة الدعم: **{role_text}**\n"
            f"قناة السجلات: **{channel_text('log_channel_id')}**\n"
            f"قناة النصوص: **{channel_text('transcript_channel_id')}**\n"
            f"الحد المفتوح لكل عضو: **{config['max_open_per_user']}**"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="تُحفظ التغييرات في نفس قاعدة البيانات التي يستخدمها الداشبورد.")
    return embed


class TicketManagementView(discord.ui.View):
    def __init__(self, cog, owner_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.add_item(TicketAdminSelect(self))


class TicketAdminSelect(discord.ui.Select):
    def __init__(self, parent: TicketManagementView):
        super().__init__(
            placeholder="اختر ما تريد إدارته من Discord",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="الإعدادات العامة", value="settings", emoji="⚙️"),
                discord.SelectOption(label="إنشاء لوحة تذاكر", value="create", emoji="➕"),
                discord.SelectOption(label="إدارة اللوحات الحالية", value="panels", emoji="🗂️"),
                discord.SelectOption(label="عرض التذاكر", value="tickets", emoji="🎫"),
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent_view
        if not _can_manage(interaction, parent.owner_id):
            return await interaction.response.send_message("هذه القائمة مخصصة للشخص الذي فتحها ولإدارة السيرفر فقط.", ephemeral=True)

        action = self.values[0]
        if action == "settings":
            config = await parent.cog.db.get_ticket_settings(interaction.guild.id)
            return await interaction.response.send_message(
                embed=_settings_embed(interaction.guild, config),
                view=TicketSettingsView(parent.cog, parent.owner_id),
                ephemeral=True,
            )
        if action == "create":
            return await interaction.response.send_message(
                "اختر قناة النشر وفئة التذاكر ورتبة الدعم، ثم اضغط زر إدخال التفاصيل.",
                view=TicketPanelBuilderView(parent.cog, parent.owner_id, guild_id=interaction.guild.id),
                ephemeral=True,
            )
        if action == "panels":
            panels = await parent.cog.db.list_ticket_panels(interaction.guild.id)
            if not panels:
                return await interaction.response.send_message("لا توجد لوحات محفوظة حالياً. استخدم خيار إنشاء لوحة تذاكر أولاً.", ephemeral=True)
            return await interaction.response.send_message(
                "اختر لوحة، ثم اختر الإجراء المطلوب.",
                view=TicketPanelManagementView(parent.cog, parent.owner_id, panels),
                ephemeral=True,
            )
        rows = await parent.cog.db.fetchall(
            "SELECT id,channel_id,user_id,status,claimed_by,created_at FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT 20",
            (interaction.guild.id,),
        )
        if not rows:
            return await interaction.response.send_message("لا توجد تذاكر مسجلة في هذا السيرفر.", ephemeral=True)
        lines = []
        for row in rows:
            channel = interaction.guild.get_channel(int(row["channel_id"] or 0))
            status = {"open": "مفتوحة", "locked": "مقفلة", "closed": "مغلقة", "deleted": "محذوفة"}.get(str(row["status"]), str(row["status"]))
            channel_text = channel.mention if channel else "القناة غير موجودة"
            claimant = f" • المستلم: <@{row['claimed_by']}>" if row["claimed_by"] else ""
            lines.append(f"**#{row['id']}** • {channel_text} • <@{row['user_id']}> • {status}{claimant}")
        embed = discord.Embed(title="آخر التذاكر", description="\n".join(lines), color=0x5865F2)
        return await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketSettingsView(discord.ui.View):
    def __init__(self, cog, owner_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.add_item(TicketDefaultCategorySelect(self))
        self.add_item(TicketDefaultRoleSelect(self))
        self.add_item(TicketTranscriptChannelSelect(self))
        self.add_item(TicketLogChannelSelect(self))
        self.add_item(TicketSettingsActionSelect(self))


class _TicketChannelSettingSelect(discord.ui.ChannelSelect):
    setting_key = ""

    def __init__(self, parent: TicketSettingsView, *, placeholder: str, channel_type: discord.ChannelType, row: int):
        super().__init__(
            channel_types=[channel_type],
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            row=row,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        selected = self.values[0]
        config = _settings(await self.parent_view.cog.db.get_ticket_settings(interaction.guild.id))
        config[self.setting_key] = selected.id
        await self.parent_view.cog.db.save_ticket_settings(interaction.guild.id, config)
        return await interaction.response.edit_message(
            content=f"تم تحديث الإعداد: {selected.mention}",
            embed=_settings_embed(interaction.guild, config),
            view=self.parent_view,
        )


class TicketDefaultCategorySelect(_TicketChannelSettingSelect):
    setting_key = "default_category_id"

    def __init__(self, parent):
        super().__init__(parent, placeholder="اختيار فئة التذاكر الافتراضية", channel_type=discord.ChannelType.category, row=0)


class TicketTranscriptChannelSelect(_TicketChannelSettingSelect):
    setting_key = "transcript_channel_id"

    def __init__(self, parent):
        super().__init__(parent, placeholder="اختيار قناة سجلات التذاكر", channel_type=discord.ChannelType.text, row=2)


class TicketLogChannelSelect(_TicketChannelSettingSelect):
    setting_key = "log_channel_id"

    def __init__(self, parent):
        super().__init__(parent, placeholder="اختيار قناة سجل الأحداث", channel_type=discord.ChannelType.text, row=3)


class TicketDefaultRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: TicketSettingsView):
        super().__init__(placeholder="اختيار رتب فريق الدعم (يمكن اختيار عدة رتب)", min_values=0, max_values=25, row=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        roles = list(self.values)
        config = _settings(await self.parent_view.cog.db.get_ticket_settings(interaction.guild.id))
        config["default_support_role_ids"] = [role.id for role in roles]
        config["default_support_role_id"] = roles[0].id if roles else None
        await self.parent_view.cog.db.save_ticket_settings(interaction.guild.id, config)
        role_text = "، ".join(role.mention for role in roles) or "غير محددة"
        return await interaction.response.edit_message(
            content=f"تم تحديث رتب الدعم: {role_text}",
            embed=_settings_embed(interaction.guild, config),
            view=self.parent_view,
        )


class TicketSettingsActionSelect(discord.ui.Select):
    def __init__(self, parent: TicketSettingsView):
        options = [
            discord.SelectOption(label=label, value=key)
            for key, label in TOGGLE_LABELS.items()
        ]
        options.extend([
            discord.SelectOption(label="إزالة الفئة الافتراضية", value="clear_category"),
            discord.SelectOption(label="إزالة رتبة الدعم", value="clear_role"),
            discord.SelectOption(label="إزالة قناة السجلات", value="clear_log"),
            discord.SelectOption(label="إزالة قناة النصوص", value="clear_transcript"),
            discord.SelectOption(label="الإعدادات المتقدمة", value="advanced", emoji="🛠️"),
        ])
        super().__init__(placeholder="تعديل خصائص نظام التذاكر", min_values=1, max_values=1, options=options, row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent_view
        if not _can_manage(interaction, parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        config = _settings(await parent.cog.db.get_ticket_settings(interaction.guild.id))
        key = self.values[0]
        clear_map = {
            "clear_category": "default_category_id",
            "clear_role": "default_support_role_id",
            "clear_log": "log_channel_id",
            "clear_transcript": "transcript_channel_id",
        }
        if key == "advanced":
            return await interaction.response.send_modal(TicketAdvancedSettingsModal(parent.cog, parent.owner_id, config))
        if key in clear_map:
            config[clear_map[key]] = None
            message = "تم حذف الإعداد المحدد."
        else:
            config[key] = not bool(config.get(key, False))
            message = f"{TOGGLE_LABELS[key]}: {'مفعّل' if config[key] else 'معطّل'}"
        await parent.cog.db.save_ticket_settings(interaction.guild.id, config)
        return await interaction.response.edit_message(
            content=message,
            embed=_settings_embed(interaction.guild, config),
            view=parent,
        )


class TicketAdvancedSettingsModal(discord.ui.Modal, title="الإعدادات المتقدمة للتذاكر"):
    def __init__(self, cog, owner_id: int, config: dict[str, Any]):
        super().__init__()
        self.cog = cog
        self.owner_id = owner_id
        self.max_open = discord.ui.TextInput(label="أقصى عدد تذاكر مفتوحة لكل عضو", default=str(config.get("max_open_per_user", 1)), max_length=2)
        self.delete_after = discord.ui.TextInput(label="الحذف التلقائي بعد الإغلاق بالثواني", default=str(config.get("delete_after_close_seconds", 0)), max_length=4)
        self.name_template = discord.ui.TextInput(label="قالب اسم قناة التذكرة", default=str(config.get("channel_name_template") or "ticket-{number}-{user}"), max_length=90, required=False)
        self.add_item(self.max_open)
        self.add_item(self.delete_after)
        self.add_item(self.name_template)

    async def on_submit(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية حفظ هذه الإعدادات.", ephemeral=True)
        try:
            max_open = max(1, min(10, int(str(self.max_open.value).strip())))
            delete_after = max(0, min(3600, int(str(self.delete_after.value).strip() or "0")))
        except ValueError:
            return await interaction.response.send_message("أدخل أرقاماً صحيحة للحد الأقصى ومدة الحذف.", ephemeral=True)
        config = _settings(await self.cog.db.get_ticket_settings(interaction.guild.id))
        config["max_open_per_user"] = max_open
        config["delete_after_close_seconds"] = delete_after
        template = str(self.name_template.value or "").strip()
        config["channel_name_template"] = template[:90] or "ticket-{number}-{user}"
        await self.cog.db.save_ticket_settings(interaction.guild.id, config)
        await interaction.response.send_message("تم حفظ الإعدادات المتقدمة للتذاكر.", ephemeral=True)


class TicketPanelManagementView(discord.ui.View):
    def __init__(self, cog, owner_id: int, panels: list[dict[str, Any]]):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.panels = panels[:25]
        self.selected_panel_id = int(self.panels[0]["id"]) if self.panels else None
        self.add_item(TicketPanelSelect(self))
        self.add_item(TicketPanelActionSelect(self))


class TicketPanelSelect(discord.ui.Select):
    def __init__(self, parent: TicketPanelManagementView):
        options = [
            discord.SelectOption(
                label=str(panel.get("title") or f"لوحة #{panel['id']}")[:100],
                description=f"لوحة رقم {panel['id']}",
                value=str(panel["id"]),
                default=int(panel["id"]) == parent.selected_panel_id,
            )
            for panel in parent.panels
        ]
        super().__init__(placeholder="اختر لوحة التذاكر", min_values=1, max_values=1, options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent_view.selected_panel_id = int(self.values[0])
        panel = await self.parent_view.cog.db.get_ticket_panel(self.parent_view.selected_panel_id)
        return await interaction.response.edit_message(
            content=f"تم اختيار **{panel.get('title') or 'لوحة التذاكر'}** (#{self.parent_view.selected_panel_id}). اختر الإجراء.",
            view=self.parent_view,
        )


class TicketPanelActionSelect(discord.ui.Select):
    def __init__(self, parent: TicketPanelManagementView):
        super().__init__(
            placeholder="إجراء على اللوحة المختارة",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="نشر اللوحة أو تحديثها", value="publish", emoji="📨"),
                discord.SelectOption(label="تعديل إعدادات اللوحة", value="edit", emoji="✏️"),
                discord.SelectOption(label="حذف اللوحة", value="delete", emoji="🗑️"),
            ],
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent_view
        if not _can_manage(interaction, parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        panel_id = parent.selected_panel_id
        if not panel_id:
            return await interaction.response.send_message("اختر لوحة أولاً.", ephemeral=True)
        action = self.values[0]
        if action == "edit":
            panel = await parent.cog.db.get_ticket_panel(panel_id)
            if not panel:
                return await interaction.response.send_message("لم تعد اللوحة موجودة.", ephemeral=True)
            return await interaction.response.send_message(
                "عدّل الاختيارات ثم افتح نموذج التفاصيل لحفظ التغييرات.",
                view=TicketPanelBuilderView(parent.cog, parent.owner_id, panel=panel, guild_id=interaction.guild.id),
                ephemeral=True,
            )
        if action == "delete":
            panel = await parent.cog.db.get_ticket_panel(panel_id)
            if not panel:
                return await interaction.response.send_message("لم تعد اللوحة موجودة.", ephemeral=True)
            return await interaction.response.send_message(
                f"هل تريد حذف لوحة **{panel.get('title') or 'التذاكر'}** نهائياً؟",
                view=TicketPanelDeleteConfirmView(parent.cog, parent.owner_id, panel),
                ephemeral=True,
            )
        try:
            panel = await parent.cog.publish_panel(panel_id)
        except Exception as exc:
            return await interaction.response.send_message(f"تعذر نشر اللوحة: {str(exc)[:250]}", ephemeral=True)
        channel = interaction.guild.get_channel(int(panel.get("channel_id") or 0))
        message_id = panel.get("message_id")
        link = f"https://discord.com/channels/{interaction.guild.id}/{channel.id}/{message_id}" if channel and message_id else ""
        return await interaction.response.send_message(f"تم نشر اللوحة أو تحديثها بنجاح. {link}", ephemeral=True)


class TicketPanelDeleteConfirmView(discord.ui.View):
    def __init__(self, cog, owner_id: int, panel: dict[str, Any]):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = owner_id
        self.panel = panel

    @discord.ui.button(label="تأكيد الحذف", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_manage(interaction, self.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية حذف هذه اللوحة.", ephemeral=True)
        guild = interaction.guild
        channel = guild.get_channel(int(self.panel.get("channel_id") or 0))
        message_id = _as_id(self.panel.get("message_id"))
        if channel and message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        await self.cog.db.delete_ticket_panel(int(self.panel["id"]))
        return await interaction.response.edit_message(content="تم حذف اللوحة وإزالة رسالتها المنشورة إن كانت موجودة.", view=None)

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("هذه القائمة ليست مخصصة لك.", ephemeral=True)
        return await interaction.response.edit_message(content="تم إلغاء عملية الحذف.", view=None)


class TicketPanelBuilderView(discord.ui.View):
    def __init__(self, cog, owner_id: int, panel: dict[str, Any] | None = None, guild_id: int | None = None):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.panel = dict(panel or {})
        self.guild_id = int(guild_id or self.panel.get("guild_id") or 0)
        self.channel_id = _as_id(self.panel.get("channel_id"))
        self.category_id = _as_id(self.panel.get("category_id"))
        raw_role_ids = self.panel.get("support_role_ids") or ([self.panel.get("support_role_id")] if self.panel.get("support_role_id") else [])
        self.support_role_ids = list(dict.fromkeys(int(value) for value in raw_role_ids if _as_id(value)))
        self.support_roles_customized = bool(
            self.panel.get("support_roles_customized")
            or (not self.panel.get("support_role_ids") and self.panel.get("support_role_id"))
        )
        self.support_role_id = self.support_role_ids[0] if self.support_role_ids else None
        self.mode = str(self.panel.get("mode") or "select")
        self.add_item(TicketBuilderPublishChannelSelect(self))
        self.add_item(TicketBuilderCategorySelect(self))
        self.add_item(TicketBuilderRoleSelect(self))
        self.add_item(TicketBuilderModeSelect(self))
        self.add_item(TicketBuilderDetailsButton(self))

    def summary(self) -> str:
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild is None:
            # New panels use the active interaction guild; IDs are still shown clearly.
            return "تم حفظ الاختيارات. اضغط إدخال التفاصيل للمتابعة."
        channel = guild.get_channel(self.channel_id or 0)
        category = guild.get_channel(self.category_id or 0)
        roles = [guild.get_role(role_id) for role_id in self.support_role_ids]
        role_text = "، ".join(role.mention for role in roles if role) or "ترث من إعدادات السيرفر"
        return (
            f"قناة النشر: {channel.mention if channel else 'لم تحدد بعد'}\n"
            f"فئة التذاكر الافتراضية: {category.name if isinstance(category, discord.CategoryChannel) else 'لم تحدد بعد'}\n"
            f"رتب الدعم: {role_text}\n"
            f"نمط اللوحة: {'قائمة اختيار' if self.mode == 'select' else 'أزرار'}"
        )


class TicketBuilderPublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="قناة نشر لوحة التذاكر", min_values=1, max_values=1, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent_view.channel_id = self.values[0].id
        return await interaction.response.edit_message(content=self.parent_view.summary(), view=self.parent_view)


class TicketBuilderCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(channel_types=[discord.ChannelType.category], placeholder="فئة قنوات التذاكر", min_values=1, max_values=1, row=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent_view.category_id = self.values[0].id
        return await interaction.response.edit_message(content=self.parent_view.summary(), view=self.parent_view)


class TicketBuilderRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(placeholder="رتب فريق الدعم لهذه اللوحة (يمكن اختيار عدة رتب)", min_values=0, max_values=25, row=2)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent_view.support_role_ids = [role.id for role in self.values]
        self.parent_view.support_roles_customized = bool(self.parent_view.support_role_ids)
        self.parent_view.support_role_id = self.parent_view.support_role_ids[0] if self.parent_view.support_role_ids else None
        self.parent_view.panel["support_roles_customized"] = self.parent_view.support_roles_customized
        if self.parent_view.support_role_ids:
            self.parent_view.panel["support_role_ids"] = list(self.parent_view.support_role_ids)
            self.parent_view.panel["support_role_id"] = self.parent_view.support_role_id
        else:
            self.parent_view.panel.pop("support_role_ids", None)
            self.parent_view.panel.pop("support_role_id", None)
        return await interaction.response.edit_message(content=self.parent_view.summary(), view=self.parent_view)


class TicketBuilderModeSelect(discord.ui.Select):
    def __init__(self, parent: TicketPanelBuilderView):
        mode = parent.mode if parent.mode in {"buttons", "select"} else "select"
        super().__init__(
            placeholder="طريقة عرض أنواع التذاكر",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="قائمة اختيار", value="select", default=mode == "select"),
                discord.SelectOption(label="أزرار منفصلة", value="buttons", default=mode == "buttons"),
            ],
            row=3,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent_view.mode = self.values[0]
        return await interaction.response.edit_message(content=self.parent_view.summary(), view=self.parent_view)


class TicketBuilderDetailsButton(discord.ui.Button):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(label="إدخال تفاصيل اللوحة", style=discord.ButtonStyle.success, emoji="💾", row=4)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        return await interaction.response.send_modal(TicketPanelDetailsModal(self.parent_view))


class TicketPanelDetailsModal(discord.ui.Modal, title="تفاصيل لوحة التذاكر"):
    def __init__(self, builder: TicketPanelBuilderView):
        super().__init__()
        self.builder = builder
        panel = builder.panel
        self.title_input = discord.ui.TextInput(
            label="عنوان لوحة التذاكر",
            default=str(panel.get("title") or "🎫 الدعم الفني")[:256],
            max_length=256,
        )
        self.description_input = discord.ui.TextInput(
            label="وصف اللوحة",
            default=str(panel.get("description") or "اختر نوع الطلب لفتح تذكرة.")[:1000],
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.image_input = discord.ui.TextInput(
            label="رابط صورة اللوحة (اختياري)",
            default=str(panel.get("image_url") or "")[:1000],
            required=False,
            max_length=1000,
        )
        self.ticket_description_input = discord.ui.TextInput(
            label="الوصف داخل التذكرة",
            default=str(panel.get("ticket_description") or "يرجى شرح المشكلة بالتفصيل.")[:1000],
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.footer_input = discord.ui.TextInput(
            label="تذييل اللوحة",
            default=str((panel.get("settings") or {}).get("footer") or "ANO BOT Support")[:100],
            required=False,
            max_length=100,
        )
        for item in (
            self.title_input,
            self.description_input,
            self.image_input,
            self.ticket_description_input,
            self.footer_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        builder = self.builder
        if not _can_manage(interaction, builder.owner_id):
            return await interaction.response.send_message(
                "ليست لديك صلاحية حفظ لوحة التذاكر.", ephemeral=True
            )
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "لا يمكن إنشاء لوحة خارج السيرفر.", ephemeral=True
            )

        # Acknowledge the modal immediately. Database access happens afterwards.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            current = builder.panel
            settings = _settings(await builder.cog.db.get_ticket_settings(guild.id))
            channel_id = builder.channel_id or _as_id(current.get("channel_id"))
            category_id = (
                builder.category_id
                or _as_id(current.get("category_id"))
                or _as_id(settings.get("default_category_id"))
            )
            if builder.support_roles_customized:
                role_ids = builder.support_role_ids
            else:
                role_ids = [
                    int(value) for value in (
                        current.get("support_role_ids")
                        or ([current.get("support_role_id")] if current.get("support_role_id") else [])
                        or settings.get("default_support_role_ids")
                        or ([settings.get("default_support_role_id")] if settings.get("default_support_role_id") else [])
                    ) if _as_id(value)
                ]
            channel = guild.get_channel(channel_id or 0)
            category = guild.get_channel(category_id or 0)
            roles = [guild.get_role(role_id) for role_id in role_ids]

            if not isinstance(channel, discord.TextChannel):
                return await interaction.followup.send(
                    "اختر قناة نصية صالحة لنشر اللوحة.", ephemeral=True
                )
            if not isinstance(category, discord.CategoryChannel):
                return await interaction.followup.send(
                    "اختر فئة صالحة لقنوات التذاكر أو اضبط الفئة الافتراضية من الإعدادات.",
                    ephemeral=True,
                )
            if any(role is None for role in roles):
                return await interaction.followup.send(
                    "إحدى رتب الدعم المحددة لم تعد موجودة. أعد اختيار رتب الدعم.", ephemeral=True
                )

            builder.panel.update({
                "channel_id": channel.id,
                "category_id": category.id,
                "support_role_id": roles[0].id if roles else None,
                "support_role_ids": [role.id for role in roles if role],
                "support_roles_customized": bool(builder.support_roles_customized),
                "title": str(self.title_input.value or "🎫 الدعم الفني").strip()[:256],
                "description": str(self.description_input.value or "اختر نوع الطلب لفتح تذكرة.").strip()[:4096],
                "image_url": _safe_url(self.image_input.value),
                "ticket_description": str(self.ticket_description_input.value or "يرجى شرح المشكلة بالتفصيل.").strip()[:2000],
            })
            panel_settings = dict(current.get("settings") or {})
            panel_settings["color"] = _safe_color(panel_settings.get("color"), "#5865F2")
            panel_settings["footer"] = str(self.footer_input.value or "ANO BOT Support").strip()[:100]
            panel_settings.setdefault("thumbnail_url", None)
            panel_settings.setdefault("select_placeholder", "اختر نوع التذكرة")
            panel_settings.setdefault("ticket_footer", "ANO BOT Support")
            panel_settings.setdefault("ticket_image_url", None)
            builder.panel["settings"] = panel_settings

            if not builder.panel.get("options"):
                builder.panel["options"] = [{
                    "name": "الدعم العام",
                    "emoji": "🎫",
                    "description": "فتح تذكرة دعم",
                    "ticket_name": "ticket-{number}-{user}",
                    "max_open": 1,
                    "enabled": True,
                }]

            option_view = TicketOptionManagerView(builder)
            await interaction.followup.send(
                content=option_view.summary(),
                view=option_view,
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                f"تعذر تجهيز خيارات التذاكر: {str(exc)[:250]}",
                ephemeral=True,
            )


class TicketOptionManagerView(discord.ui.View):
    """Button-based manager for adding, editing and deleting ticket types."""

    def __init__(self, builder: TicketPanelBuilderView):
        super().__init__(timeout=900)
        self.builder = builder
        self.owner_id = builder.owner_id
        self.options = builder.panel.setdefault("options", [])
        if not self.options:
            self.options.append({
                "name": "الدعم العام",
                "emoji": "🎫",
                "description": "فتح تذكرة دعم",
                "ticket_name": "ticket-{number}-{user}",
                "max_open": 1,
                "enabled": True,
            })
        self.selected_index = 0
        self.add_item(TicketOptionSelect(self))
        self.edit_option.disabled = not bool(self.options)
        self.delete_option.disabled = len(self.options) <= 1
        self.add_option.disabled = len(self.options) >= 25

    def refresh_select(self) -> None:
        for child in list(self.children):
            if isinstance(child, TicketOptionSelect):
                self.remove_item(child)
        if self.options:
            self.add_item(TicketOptionSelect(self))

    def summary(self) -> str:
        lines = [
            "**إدارة أنواع التذاكر**",
            "استعمل الأزرار لإضافة نوع أو تعديل النوع المحدد أو حذفه.",
            "",
        ]
        for index, option in enumerate(self.options, start=1):
            selected = "  ← محدد" if index - 1 == self.selected_index else ""
            status = "مفعّل" if option.get("enabled", True) else "معطّل"
            lines.append(
                f"**{index}.** {option.get('emoji') or '🎫'} "
                f"**{option.get('name') or 'نوع بلا اسم'}** "
                f"· {status}{selected}\n"
                f"الوصف: {str(option.get('description') or 'بلا وصف')[:100]}"
            )
        lines.extend(["", f"عدد الأنواع: **{len(self.options)}/25**", "بعد الانتهاء اضغط **حفظ ونشر اللوحة**."])
        return "\n".join(lines)[:1850]

    async def _check(self, interaction: discord.Interaction) -> bool:
        if not _can_manage(interaction, self.owner_id):
            await interaction.response.send_message(
                "هذه القائمة مخصصة لمنشئها وإدارة السيرفر فقط.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="إضافة نوع", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def add_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        if len(self.options) >= 25:
            return await interaction.response.send_message(
                "وصلت إلى الحد الأقصى وهو 25 نوعاً.", ephemeral=True
            )
        await interaction.response.send_modal(TicketOptionModal(self.builder))

    @discord.ui.button(label="تعديل المحدد", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        if not self.options:
            return await interaction.response.send_message(
                "لا توجد أنواع لتعديلها.", ephemeral=True
            )
        await interaction.response.send_modal(
            TicketOptionModal(self.builder, self.selected_index)
        )

    @discord.ui.button(label="حذف المحدد", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        if len(self.options) <= 1:
            return await interaction.response.send_message(
                "يجب الاحتفاظ بنوع واحد على الأقل. عدّل النوع الأخير بدلاً من حذفه.",
                ephemeral=True,
            )
        removed = self.options.pop(self.selected_index)
        self.selected_index = min(self.selected_index, len(self.options) - 1)
        self.refresh_select()
        self.edit_option.disabled = False
        self.delete_option.disabled = len(self.options) <= 1
        self.add_option.disabled = len(self.options) >= 25
        await interaction.response.edit_message(
            content=f"تم حذف نوع **{removed.get('name') or 'التذكرة'}**.\n\n{self.summary()}",
            view=self,
        )

    @discord.ui.button(label="الفئة ورتب النوع", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def route_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        if not self.options:
            return await interaction.response.send_message("لا توجد أنواع لتخصيصها.", ephemeral=True)
        view = TicketOptionRoutingView(self.builder, self.selected_index)
        await interaction.response.send_message(view.summary(), view=view, ephemeral=True)

    @discord.ui.button(label="حفظ ونشر اللوحة", emoji="💾", style=discord.ButtonStyle.success, row=3)
    async def save_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "لا يمكن حفظ لوحة خارج السيرفر.", ephemeral=True
            )
        if not self.options:
            return await interaction.response.send_message(
                "أضف نوع تذكرة واحداً على الأقل قبل الحفظ.", ephemeral=True
            )

        # Defer before database calls and Discord message publication.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            panel = self.builder.panel
            channel = guild.get_channel(_as_id(panel.get("channel_id")) or 0)
            category = guild.get_channel(_as_id(panel.get("category_id")) or 0)
            role_ids = panel.get("support_role_ids") or ([panel.get("support_role_id")] if panel.get("support_role_id") else [])
            roles = [guild.get_role(int(value)) for value in role_ids if _as_id(value)]
            if not isinstance(channel, discord.TextChannel):
                return await interaction.followup.send(
                    "قناة نشر اللوحة غير صالحة. أعد اختيارها.", ephemeral=True
                )
            if not isinstance(category, discord.CategoryChannel):
                return await interaction.followup.send(
                    "فئة قنوات التذاكر غير صالحة. أعد اختيارها.", ephemeral=True
                )
            if any(role is None for role in roles):
                return await interaction.followup.send(
                    "إحدى رتب الدعم لم تعد موجودة. أعد اختيارها.", ephemeral=True
                )

            options = []
            for raw in self.options[:25]:
                option_category_id = _as_id(raw.get("category_id"))
                if option_category_id:
                    option_category = guild.get_channel(option_category_id)
                    if not isinstance(option_category, discord.CategoryChannel):
                        option_category_id = None
                raw_role_ids = raw.get("support_role_ids") or ([raw.get("support_role_id")] if raw.get("support_role_id") else [])
                option_role_ids = list(dict.fromkeys(int(value) for value in raw_role_ids if _as_id(value)))
                option_roles = [guild.get_role(role_id) for role_id in option_role_ids]
                if any(role is None for role in option_roles):
                    return await interaction.followup.send(
                        f"إحدى رتب الدعم الخاصة بالنوع {str(raw.get('name') or 'التذكرة')[:60]} لم تعد موجودة.",
                        ephemeral=True,
                    )
                option_role_id = option_role_ids[0] if option_role_ids else None
                try:
                    max_open = max(1, min(10, int(raw.get("max_open", 1) or 1)))
                except (TypeError, ValueError):
                    max_open = 1
                options.append({
                    "name": str(raw.get("name") or "فتح تذكرة").strip()[:80],
                    "emoji": str(raw.get("emoji") or "🎫").strip()[:20],
                    "description": str(raw.get("description") or "فتح تذكرة").strip()[:100],
                    "ticket_name": str(raw.get("ticket_name") or "ticket-{number}-{user}").strip()[:90],
                    "category_id": option_category_id,
                    "support_role_id": option_role_id,
                    "support_role_ids": option_role_ids,
                    "support_roles_customized": bool(raw.get("support_roles_customized", bool(option_role_ids))),
                    "color": _safe_color(raw.get("color"), "#5865F2"),
                    "image_url": _safe_url(raw.get("image_url")),
                    "footer": str(raw.get("footer") or "ANO BOT Support").strip()[:100],
                    "priority": str(raw.get("priority") or "normal").strip()[:20],
                    "max_open": max_open,
                    "button_style": str(raw.get("button_style") or "primary").lower()
                    if str(raw.get("button_style") or "primary").lower() in {"primary", "secondary", "success", "danger"}
                    else "primary",
                    "enabled": bool(raw.get("enabled", True)),
                })
            if not any(item["enabled"] for item in options):
                return await interaction.followup.send(
                    "فعّل نوع تذكرة واحداً على الأقل قبل النشر.", ephemeral=True
                )

            settings = dict(panel.get("settings") or {})
            settings["color"] = _safe_color(settings.get("color"), "#5865F2")
            payload = {
                "channel_id": channel.id,
                "message_id": _as_id(panel.get("message_id")),
                "title": str(panel.get("title") or "🎫 الدعم الفني").strip()[:256],
                "description": str(panel.get("description") or "اختر نوع الطلب لفتح تذكرة.").strip()[:4096],
                "image_url": _safe_url(panel.get("image_url")),
                "mode": self.builder.mode if self.builder.mode in {"buttons", "select"} else "select",
                "button_label": "فتح تذكرة",
                "button_emoji": "🎫",
                "category_id": category.id,
                "support_role_id": roles[0].id if roles else None,
                "support_role_ids": [role.id for role in roles if role],
                "support_roles_customized": bool(panel.get("support_roles_customized", False)),
                "ticket_description": str(panel.get("ticket_description") or "يرجى شرح المشكلة بالتفصيل.")[:2000],
                "options": options,
                "settings": settings,
            }

            panel_id = _as_id(panel.get("id"))
            old_channel_id = _as_id(panel.get("channel_id"))
            if panel_id:
                if old_channel_id and old_channel_id != channel.id and panel.get("message_id"):
                    old_channel = guild.get_channel(old_channel_id)
                    if isinstance(old_channel, discord.TextChannel):
                        try:
                            old_message = await old_channel.fetch_message(int(panel["message_id"]))
                            await old_message.delete()
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass
                    payload["message_id"] = None
                updated = await self.builder.cog.db.update_ticket_panel(panel_id, payload)
                if updated is False:
                    return await interaction.followup.send(
                        "تعذر تحديث اللوحة في قاعدة البيانات.", ephemeral=True
                    )
            else:
                payload["guild_id"] = guild.id
                panel_id = await self.builder.cog.db.create_ticket_panel(payload)
                panel["id"] = panel_id

            try:
                saved = await self.builder.cog.publish_panel(panel_id)
            except Exception as exc:
                return await interaction.followup.send(
                    f"تم حفظ الإعدادات، لكن تعذر نشر اللوحة: {str(exc)[:250]}",
                    ephemeral=True,
                )

            published_channel = guild.get_channel(int(saved.get("channel_id") or 0))
            message_id = saved.get("message_id")
            link = (
                f"https://discord.com/channels/{guild.id}/{published_channel.id}/{message_id}"
                if published_channel and message_id else ""
            )
            await interaction.followup.send(
                f"تم حفظ لوحة التذاكر ونشرها بنجاح. {link}",
                ephemeral=True,
            )
            self.stop()
        except Exception as exc:
            await interaction.followup.send(
                f"وقع خطأ أثناء حفظ اللوحة: {str(exc)[:250]}",
                ephemeral=True,
            )


class TicketOptionSelect(discord.ui.Select):
    def __init__(self, manager: TicketOptionManagerView):
        options = [
            discord.SelectOption(
                label=str(option.get("name") or f"نوع #{index + 1}")[:100],
                description=str(option.get("description") or "نوع تذكرة")[:100],
                value=str(index),
                emoji=str(option.get("emoji") or "🎫")[:20],
                default=index == manager.selected_index,
            )
            for index, option in enumerate(manager.options[:25])
        ]
        super().__init__(
            placeholder="اختر نوع التذكرة الذي تريد تعديله",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.manager = manager

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.manager.owner_id):
            return await interaction.response.send_message(
                "ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True
            )
        self.manager.selected_index = int(self.values[0])
        self.manager.refresh_select()
        await interaction.response.edit_message(
            content=self.manager.summary(),
            view=self.manager,
        )


class TicketOptionRoutingView(discord.ui.View):
    """Optional category and multi-role overrides for an individual ticket type."""

    def __init__(self, builder: TicketPanelBuilderView, option_index: int):
        super().__init__(timeout=900)
        self.builder = builder
        self.owner_id = builder.owner_id
        self.option_index = option_index
        self.add_item(TicketOptionCategorySelect(self))
        self.add_item(TicketOptionRolesSelect(self))

    @property
    def option(self) -> dict[str, Any]:
        return self.builder.panel["options"][self.option_index]

    def summary(self) -> str:
        option = self.option
        guild = self.builder.cog.bot.get_guild(self.builder.guild_id)
        category_id = _as_id(option.get("category_id"))
        category = guild.get_channel(category_id) if guild and category_id else None
        role_ids = option.get("support_role_ids") or ([option.get("support_role_id")] if option.get("support_role_id") else [])
        roles = [guild.get_role(int(value)) for value in role_ids if guild and _as_id(value)]
        role_text = "، ".join(role.mention for role in roles if role) or "رتب اللوحة الافتراضية"
        category_text = category.name if isinstance(category, discord.CategoryChannel) else "فئة اللوحة الافتراضية"
        return (
            f"**تخصيص النوع: {option.get('name') or 'التذكرة'}**\n"
            f"الفئة: **{category_text}**\n"
            f"رتب الدعم: {role_text}\n\n"
            "اترك الفئة أو الرتب دون تخصيص لاستعمال إعدادات اللوحة العامة."
        )

    @discord.ui.button(label="إلغاء تخصيص الفئة والرتب", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def reset_overrides(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_manage(interaction, self.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية.", ephemeral=True)
        self.option.pop("category_id", None)
        self.option.pop("support_role_id", None)
        self.option.pop("support_role_ids", None)
        self.option["support_roles_customized"] = False
        await interaction.response.edit_message(content=self.summary(), view=self)


class TicketOptionCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, parent: TicketOptionRoutingView):
        super().__init__(
            channel_types=[discord.ChannelType.category],
            placeholder="فئة خاصة لهذا النوع (اختياري)",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية.", ephemeral=True)
        self.parent_view.option["category_id"] = self.values[0].id
        await interaction.response.edit_message(content=self.parent_view.summary(), view=self.parent_view)


class TicketOptionRolesSelect(discord.ui.RoleSelect):
    def __init__(self, parent: TicketOptionRoutingView):
        super().__init__(
            placeholder="رتب خاصة لهذا النوع (اختياري، متعدد)",
            min_values=0,
            max_values=25,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent_view.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية.", ephemeral=True)
        selected_ids = [role.id for role in self.values]
        self.parent_view.option.pop("support_role_id", None)
        if selected_ids:
            self.parent_view.option["support_role_ids"] = selected_ids
            self.parent_view.option["support_roles_customized"] = True
        else:
            self.parent_view.option.pop("support_role_ids", None)
            self.parent_view.option["support_roles_customized"] = False
        await interaction.response.edit_message(content=self.parent_view.summary(), view=self.parent_view)


class TicketOptionModal(discord.ui.Modal, title="إعدادات نوع التذكرة"):
    def __init__(self, builder: TicketPanelBuilderView, option_index: int | None = None):
        super().__init__()
        self.builder = builder
        self.option_index = option_index
        options = builder.panel.get("options") or []
        current = options[option_index] if option_index is not None and option_index < len(options) else {}
        self.name_input = discord.ui.TextInput(
            label="اسم نوع التذكرة",
            default=str(current.get("name") or "")[:80] if current else "",
            placeholder="مثال: الدعم الفني",
            max_length=80,
        )
        self.emoji_input = discord.ui.TextInput(
            label="الإيموجي",
            default=str(current.get("emoji") or "🎫")[:20],
            max_length=20,
        )
        self.description_input = discord.ui.TextInput(
            label="وصف الخيار",
            default=str(current.get("description") or "")[:100],
            style=discord.TextStyle.paragraph,
            max_length=100,
        )
        self.channel_name_input = discord.ui.TextInput(
            label="قالب اسم القناة",
            default=str(current.get("ticket_name") or "ticket-{number}-{user}")[:90],
            max_length=90,
        )
        self.max_open_input = discord.ui.TextInput(
            label="أقصى تذاكر مفتوحة من هذا النوع",
            default=str(current.get("max_open", 1)),
            max_length=2,
        )
        for item in (
            self.name_input,
            self.emoji_input,
            self.description_input,
            self.channel_name_input,
            self.max_open_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.builder.owner_id):
            return await interaction.response.send_message(
                "ليست لديك صلاحية حفظ نوع التذكرة.", ephemeral=True
            )
        name = str(self.name_input.value or "").strip()
        if not name:
            return await interaction.response.send_message(
                "اسم نوع التذكرة مطلوب.", ephemeral=True
            )
        try:
            max_open = max(1, min(10, int(str(self.max_open_input.value).strip())))
        except (TypeError, ValueError):
            return await interaction.response.send_message(
                "أدخل رقماً صحيحاً للحد الأقصى من 1 إلى 10.", ephemeral=True
            )

        options = self.builder.panel.setdefault("options", [])
        if self.option_index is not None and self.option_index >= len(options):
            return await interaction.response.send_message(
                "لم يعد نوع التذكرة المحدد موجوداً. افتح قائمة الإدارة من جديد.",
                ephemeral=True,
            )
        item = dict(options[self.option_index]) if self.option_index is not None else {}
        item.update({
            "name": name[:80],
            "emoji": str(self.emoji_input.value or "🎫").strip()[:20] or "🎫",
            "description": str(self.description_input.value or "فتح تذكرة").strip()[:100],
            "ticket_name": str(self.channel_name_input.value or "ticket-{number}-{user}").strip()[:90],
            "max_open": max_open,
            "enabled": True,
        })
        if self.option_index is None:
            if len(options) >= 25:
                return await interaction.response.send_message(
                    "وصلت إلى الحد الأقصى وهو 25 نوعاً.", ephemeral=True
                )
            options.append(item)
            selected_index = len(options) - 1
        else:
            options[self.option_index] = item
            selected_index = self.option_index

        view = TicketOptionManagerView(self.builder)
        view.selected_index = selected_index
        # Rebuild the select menu so the new label and options are visible immediately.
        view.refresh_select()
        view.edit_option.disabled = not bool(view.options)
        view.delete_option.disabled = len(view.options) <= 1
        view.add_option.disabled = len(view.options) >= 25
        await interaction.response.edit_message(
            content=view.summary(),
            view=view,
        )
