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
    "allow_lock": "السماح بقفل التذكرة",
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

    role_id = _as_id(config.get("default_support_role_id"))
    role = guild.get_role(role_id) if role_id else None
    role_text = role.mention if role else "غير محددة"
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
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent
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
                view=TicketPanelBuilderView(parent.cog, parent.owner_id),
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
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        selected = self.values[0]
        config = _settings(await self.parent.cog.db.get_ticket_settings(interaction.guild.id))
        config[self.setting_key] = selected.id
        await self.parent.cog.db.save_ticket_settings(interaction.guild.id, config)
        return await interaction.response.edit_message(
            content=f"تم تحديث الإعداد: {selected.mention}",
            embed=_settings_embed(interaction.guild, config),
            view=self.parent,
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
        super().__init__(placeholder="اختيار رتبة فريق الدعم", min_values=1, max_values=1, row=1)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        role = self.values[0]
        config = _settings(await self.parent.cog.db.get_ticket_settings(interaction.guild.id))
        config["default_support_role_id"] = role.id
        await self.parent.cog.db.save_ticket_settings(interaction.guild.id, config)
        return await interaction.response.edit_message(
            content=f"تم تحديث رتبة الدعم: {role.mention}",
            embed=_settings_embed(interaction.guild, config),
            view=self.parent,
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
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent
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
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent.selected_panel_id = int(self.values[0])
        panel = await self.parent.cog.db.get_ticket_panel(self.parent.selected_panel_id)
        return await interaction.response.edit_message(
            content=f"تم اختيار **{panel.get('title') or 'لوحة التذاكر'}** (#{self.parent.selected_panel_id}). اختر الإجراء.",
            view=self.parent,
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
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        parent = self.parent
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
                view=TicketPanelBuilderView(parent.cog, parent.owner_id, panel=panel),
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
    def __init__(self, cog, owner_id: int, panel: dict[str, Any] | None = None):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.panel = dict(panel or {})
        self.channel_id = _as_id(self.panel.get("channel_id"))
        self.category_id = _as_id(self.panel.get("category_id"))
        self.support_role_id = _as_id(self.panel.get("support_role_id"))
        self.mode = str(self.panel.get("mode") or "select")
        self.add_item(TicketBuilderPublishChannelSelect(self))
        self.add_item(TicketBuilderCategorySelect(self))
        self.add_item(TicketBuilderRoleSelect(self))
        self.add_item(TicketBuilderModeSelect(self))
        self.add_item(TicketBuilderDetailsButton(self))

    def summary(self) -> str:
        guild = self.cog.bot.get_guild(int(self.panel.get("guild_id") or 0))
        if guild is None:
            # New panels use the active interaction guild; IDs are still shown clearly.
            return "تم حفظ الاختيارات. اضغط إدخال التفاصيل للمتابعة."
        channel = guild.get_channel(self.channel_id or 0)
        category = guild.get_channel(self.category_id or 0)
        role = guild.get_role(self.support_role_id or 0)
        return (
            f"قناة النشر: {channel.mention if channel else 'لم تحدد بعد'}\n"
            f"فئة التذاكر: {category.name if isinstance(category, discord.CategoryChannel) else 'لم تحدد بعد'}\n"
            f"رتبة الدعم: {role.mention if role else 'لم تحدد بعد'}\n"
            f"نمط اللوحة: {'قائمة اختيار' if self.mode == 'select' else 'أزرار'}"
        )


class TicketBuilderPublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="قناة نشر لوحة التذاكر", min_values=1, max_values=1, row=0)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent.channel_id = self.values[0].id
        return await interaction.response.edit_message(content=self.parent.summary(), view=self.parent)


class TicketBuilderCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(channel_types=[discord.ChannelType.category], placeholder="فئة قنوات التذاكر", min_values=1, max_values=1, row=1)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent.category_id = self.values[0].id
        return await interaction.response.edit_message(content=self.parent.summary(), view=self.parent)


class TicketBuilderRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(placeholder="رتبة فريق الدعم لهذه اللوحة", min_values=1, max_values=1, row=2)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent.support_role_id = self.values[0].id
        return await interaction.response.edit_message(content=self.parent.summary(), view=self.parent)


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
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        self.parent.mode = self.values[0]
        return await interaction.response.edit_message(content=self.parent.summary(), view=self.parent)


class TicketBuilderDetailsButton(discord.ui.Button):
    def __init__(self, parent: TicketPanelBuilderView):
        super().__init__(label="إدخال التفاصيل وحفظ اللوحة", style=discord.ButtonStyle.success, emoji="💾", row=4)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage(interaction, self.parent.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية استخدام هذه القائمة.", ephemeral=True)
        return await interaction.response.send_modal(TicketPanelDetailsModal(self.parent))


class TicketPanelDetailsModal(discord.ui.Modal, title="تفاصيل لوحة التذاكر"):
    def __init__(self, builder: TicketPanelBuilderView):
        super().__init__()
        self.builder = builder
        panel = builder.panel
        self.title_input = discord.ui.TextInput(label="عنوان اللوحة", default=str(panel.get("title") or "🎫 الدعم الفني")[:256], max_length=256)
        self.description_input = discord.ui.TextInput(label="وصف اللوحة", default=str(panel.get("description") or "اختر القسم المناسب لفتح تذكرة.")[:1000], style=discord.TextStyle.paragraph, max_length=1000)
        self.image_input = discord.ui.TextInput(label="رابط صورة اللوحة (اختياري)", default=str(panel.get("image_url") or "")[:1000], required=False, max_length=1000)
        self.ticket_description_input = discord.ui.TextInput(label="الوصف داخل التذكرة", default=str(panel.get("ticket_description") or "يرجى شرح المشكلة بالتفصيل.")[:1000], style=discord.TextStyle.paragraph, max_length=1000)
        current_json = {
            "options": panel.get("options") or [
                {"name": "الدعم العام", "emoji": "🎫", "description": "فتح تذكرة دعم", "priority": "normal", "max_open": 1}
            ],
            "settings": panel.get("settings") or {
                "color": "#5865F2",
                "footer": "ANO BOT Support",
                "select_placeholder": "اختر نوع التذكرة",
                "ticket_footer": "ANO BOT Support",
            },
        }
        encoded = json.dumps(current_json, ensure_ascii=False, separators=(",", ":"))
        config_kwargs = {"default": encoded} if len(encoded) <= 4000 else {}
        self.config_input = discord.ui.TextInput(
            label="أنواع التذاكر والتخصيص بصيغة JSON",
            placeholder='{"options":[{"name":"الدعم العام","description":"افتح طلباً"}],"settings":{"color":"#5865F2"}}',
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            **config_kwargs,
        )
        for item in (self.title_input, self.description_input, self.image_input, self.ticket_description_input, self.config_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        builder = self.builder
        if not _can_manage(interaction, builder.owner_id):
            return await interaction.response.send_message("ليست لديك صلاحية حفظ لوحة التذاكر.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("لا يمكن إنشاء لوحة خارج السيرفر.", ephemeral=True)
        current = builder.panel
        settings = _settings(await builder.cog.db.get_ticket_settings(guild.id))
        channel_id = builder.channel_id or _as_id(current.get("channel_id"))
        category_id = builder.category_id or _as_id(current.get("category_id")) or _as_id(settings.get("default_category_id"))
        role_id = builder.support_role_id or _as_id(current.get("support_role_id")) or _as_id(settings.get("default_support_role_id"))
        channel = guild.get_channel(channel_id or 0)
        category = guild.get_channel(category_id or 0)
        role = guild.get_role(role_id or 0) if role_id else None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("اختر قناة نصية صالحة لنشر اللوحة.", ephemeral=True)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("اختر فئة صالحة لقنوات التذاكر أو اضبط الفئة الافتراضية من الإعدادات.", ephemeral=True)
        if role_id and role is None:
            return await interaction.response.send_message("رتبة الدعم المحددة لم تعد موجودة.", ephemeral=True)

        raw_config_text = str(self.config_input.value or "").strip()
        if raw_config_text:
            try:
                raw_config = json.loads(raw_config_text)
            except json.JSONDecodeError as exc:
                return await interaction.response.send_message(f"صيغة JSON غير صحيحة قرب الحرف {exc.pos}.", ephemeral=True)
        else:
            raw_config = {}
        if isinstance(raw_config, list):
            raw_options, raw_panel_settings = raw_config, current.get("settings") or {}
        elif isinstance(raw_config, dict):
            raw_options = raw_config.get("options", current.get("options") or [])
            raw_panel_settings = raw_config.get("settings", current.get("settings") or {})
        else:
            return await interaction.response.send_message("يجب أن تكون بيانات التخصيص قائمة أو كائناً بصيغة JSON.", ephemeral=True)
        if not isinstance(raw_options, list):
            return await interaction.response.send_message("يجب أن تكون options قائمة.", ephemeral=True)
        if not isinstance(raw_panel_settings, dict):
            raw_panel_settings = {}

        options = []
        for raw in raw_options[:25]:
            if not isinstance(raw, dict):
                continue
            option_category = _as_id(raw.get("category_id")) or category_id
            option_role = _as_id(raw.get("support_role_id")) or role_id
            if option_category and not isinstance(guild.get_channel(option_category), discord.CategoryChannel):
                option_category = category_id
            if option_role and guild.get_role(option_role) is None:
                option_role = role_id
            try:
                max_open = max(1, min(10, int(raw.get("max_open", 1) or 1)))
            except (TypeError, ValueError):
                max_open = 1
            style = str(raw.get("button_style") or "primary").lower()
            if style not in {"primary", "secondary", "success", "danger"}:
                style = "primary"
            color = _safe_color(raw.get("color"), "#5865F2")
            options.append({
                "name": str(raw.get("name") or "فتح تذكرة").strip()[:80],
                "emoji": str(raw.get("emoji") or "🎫").strip()[:20],
                "description": str(raw.get("description") or "فتح تذكرة").strip()[:100],
                "ticket_name": str(raw.get("ticket_name") or "ticket-{number}-{user}").strip()[:90],
                "category_id": option_category,
                "support_role_id": option_role,
                "color": color,
                "image_url": _safe_url(raw.get("image_url")),
                "footer": str(raw.get("footer") or "ANO BOT Support").strip()[:100],
                "priority": str(raw.get("priority") or "normal").strip()[:20],
                "max_open": max_open,
                "button_style": style,
                "enabled": _as_bool(raw.get("enabled"), True),
            })
        if not options:
            options = [{
                "name": "الدعم العام",
                "emoji": "🎫",
                "description": "فتح تذكرة دعم",
                "ticket_name": "ticket-{number}-{user}",
                "category_id": category_id,
                "support_role_id": role_id,
                "color": "#5865F2",
                "image_url": None,
                "footer": "ANO BOT Support",
                "priority": "normal",
                "max_open": 1,
                "button_style": "primary",
                "enabled": True,
            }]
        if not any(option.get("enabled", True) for option in options):
            return await interaction.response.send_message("فعّل نوعاً واحداً على الأقل من أنواع التذاكر.", ephemeral=True)
        if any(option.get("enabled", True) and not option.get("category_id") for option in options):
            return await interaction.response.send_message("كل نوع مفعّل يحتاج إلى فئة تذاكر صالحة.", ephemeral=True)

        panel_settings = {
            "color": _safe_color(raw_panel_settings.get("color"), "#5865F2"),
            "thumbnail_url": _safe_url(raw_panel_settings.get("thumbnail_url")),
            "footer": str(raw_panel_settings.get("footer") or "ANO BOT Support").strip()[:100],
            "select_placeholder": str(raw_panel_settings.get("select_placeholder") or "اختر نوع التذكرة").strip()[:100],
            "ticket_footer": str(raw_panel_settings.get("ticket_footer") or "ANO BOT Support").strip()[:100],
            "ticket_image_url": _safe_url(raw_panel_settings.get("ticket_image_url")),
        }
        payload = {
            "channel_id": channel.id,
            "message_id": _as_id(current.get("message_id")),
            "title": str(self.title_input.value or "🎫 الدعم الفني").strip()[:256],
            "description": str(self.description_input.value or "اختر القسم المناسب لفتح تذكرة.").strip()[:4096],
            "image_url": _safe_url(self.image_input.value),
            "mode": builder.mode if builder.mode in {"buttons", "select"} else "select",
            "button_label": "فتح تذكرة",
            "button_emoji": "🎫",
            "category_id": category.id,
            "support_role_id": role.id if role else None,
            "ticket_description": str(self.ticket_description_input.value or "يرجى شرح المشكلة بالتفصيل.").strip()[:2000],
            "options": options,
            "settings": panel_settings,
        }

        panel_id = _as_id(current.get("id"))
        old_channel_id = _as_id(current.get("channel_id"))
        if panel_id:
            if old_channel_id and old_channel_id != channel.id and current.get("message_id"):
                old_channel = guild.get_channel(old_channel_id)
                if isinstance(old_channel, discord.TextChannel):
                    try:
                        old_message = await old_channel.fetch_message(int(current["message_id"]))
                        await old_message.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                payload["message_id"] = None
            await builder.cog.db.update_ticket_panel(panel_id, payload)
        else:
            payload["guild_id"] = guild.id
            panel_id = await builder.cog.db.create_ticket_panel(payload)

        try:
            saved = await builder.cog.publish_panel(panel_id)
        except Exception as exc:
            return await interaction.response.send_message(
                f"تم حفظ إعدادات اللوحة، لكن تعذر نشرها: {str(exc)[:250]}",
                ephemeral=True,
            )
        published_channel = guild.get_channel(int(saved.get("channel_id") or 0))
        message_id = saved.get("message_id")
        link = f"https://discord.com/channels/{guild.id}/{published_channel.id}/{message_id}" if published_channel and message_id else ""
        return await interaction.response.send_message(f"تم حفظ اللوحة ونشرها بنجاح. {link}", ephemeral=True)
