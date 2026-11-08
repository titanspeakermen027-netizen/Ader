"""Ader Professional Ticket System.

Arabic-first ticket management with persistent panels, per-type routing,
staff claiming, locking/reopening, transcripts, ratings and full dashboard
customization. Existing ticket/panel database rows remain compatible.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import EmbedColor
from utils.permissions import is_admin

MAX_OPTIONS = 25
MAX_OPEN_PER_USER = 10

DEFAULT_TICKET_SETTINGS: dict[str, Any] = {
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

BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def clean_name(value: str, fallback: str = "ticket") -> str:
    value = re.sub(r"[^a-zA-Z0-9؀-ۿ_-]+", "-", str(value).strip().lower()).strip("-")
    return value[:90] or fallback


def valid_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith(("https://", "http://")):
        return None
    return text[:1000]


def valid_color(value: Any, fallback: int = 0x5865F2) -> int:
    text = str(value or "").strip().lstrip("#")
    try:
        number = int(text, 16)
        return number if 0 <= number <= 0xFFFFFF else fallback
    except ValueError:
        return fallback


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def merge_settings(value: Any) -> dict[str, Any]:
    result = dict(DEFAULT_TICKET_SETTINGS)
    if isinstance(value, dict):
        result.update(value)
    result["max_open_per_user"] = max(1, min(MAX_OPEN_PER_USER, int(result.get("max_open_per_user", 1) or 1)))
    result["delete_after_close_seconds"] = max(0, min(3600, int(result.get("delete_after_close_seconds", 0) or 0)))
    return result


class CloseTicketModal(discord.ui.Modal, title="إغلاق التذكرة"):
    reason = discord.ui.TextInput(
        label="سبب الإغلاق",
        placeholder="اكتب سبب إغلاق التذكرة...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog._close_ticket(interaction, self.channel_id, str(self.reason.value or "").strip())


class RenameTicketModal(discord.ui.Modal, title="إعادة تسمية التذكرة"):
    name = discord.ui.TextInput(
        label="اسم القناة الجديد",
        placeholder="مثال: ticket-12345",
        min_length=1,
        max_length=90,
    )

    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket or ticket["status"] not in {"open", "locked"}:
            return await interaction.response.send_message("لا يمكن إعادة تسمية هذه التذكرة حالياً.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
        value = clean_name(str(self.name.value), f"ticket-{ticket['id']}")
        try:
            await interaction.channel.edit(name=value, reason=f"إعادة تسمية التذكرة #{ticket['id']} بواسطة {interaction.user}")
        except discord.HTTPException:
            return await interaction.response.send_message("تعذر تغيير اسم القناة. تحقق من صلاحيات البوت.", ephemeral=True)
        await self.cog.write_ticket_data(ticket, {"name": value})
        await interaction.response.send_message(f"تمت إعادة تسمية التذكرة إلى `{value}`.", ephemeral=True)


class RatingCommentModal(discord.ui.Modal, title="تعليق التقييم"):
    comment = discord.ui.TextInput(
        label="تعليق اختياري",
        placeholder="اكتب ملاحظتك حول تجربة الدعم...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: "TicketManager", ticket_id: int, rating: int):
        super().__init__()
        self.cog = cog
        self.ticket_id = ticket_id
        self.rating = rating

    async def on_submit(self, interaction: discord.Interaction):
        ticket = await self.cog.db.get_ticket(self.ticket_id)
        if not ticket:
            return await interaction.response.send_message("لم تعد التذكرة موجودة.", ephemeral=True)
        if int(ticket["user_id"] or 0) != interaction.user.id:
            return await interaction.response.send_message("هذا التقييم ليس مخصصاً لك.", ephemeral=True)
        data = self.cog.ticket_data(ticket)
        await self.cog.save_rating(ticket, self.rating, str(self.comment.value or "").strip())
        await interaction.response.send_message("تم تسجيل تقييمك. شكراً لملاحظتك.", ephemeral=True)
        try:
            await interaction.message.edit(view=discord.ui.View(timeout=1))
        except Exception:
            pass
        await self.cog.log_event(ticket, "rating", {"rating": self.rating, "comment": str(self.comment.value or "").strip(), "staff_id": data.get("claimed_by")})


class RatingView(discord.ui.View):
    def __init__(self, cog: "TicketManager", ticket_id: int):
        super().__init__(timeout=86400)
        self.cog = cog
        self.ticket_id = ticket_id
        for rating in range(1, 6):
            button = discord.ui.Button(label=str(rating), emoji="⭐", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:rate:{ticket_id}:{rating}")
            button.callback = self._callback(rating)
            self.add_item(button)

    def _callback(self, rating: int):
        async def callback(interaction: discord.Interaction):
            await interaction.response.send_modal(RatingCommentModal(self.cog, self.ticket_id, rating))
        return callback


class AddMemberView(discord.ui.View):
    def __init__(self, cog: "TicketManager", channel_id: int, mode: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.channel_id = channel_id
        self.mode = mode
        self.add_item(self.MemberSelect(self))

    class MemberSelect(discord.ui.UserSelect):
        def __init__(self, parent: "AddMemberView"):
            super().__init__(placeholder="اختر عضواً", min_values=1, max_values=1)
            self.parent = parent

        async def callback(self, interaction: discord.Interaction):
            ticket = await self.parent.cog.get_ticket_by_channel(self.parent.channel_id)
            if not ticket:
                return await interaction.response.send_message("التذكرة غير موجودة.", ephemeral=True)
            if not await self.parent.cog.is_staff(interaction, ticket):
                return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
            member = self.values[0]
            if not isinstance(member, discord.Member):
                return await interaction.response.send_message("تعذر تحديد العضو.", ephemeral=True)
            if self.parent.mode == "add":
                await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, reason=f"إضافة عضو إلى التذكرة #{ticket['id']}")
                await interaction.response.send_message(f"تمت إضافة {member.mention} إلى التذكرة.", ephemeral=True)
            else:
                if member.id == int(ticket["user_id"]):
                    return await interaction.response.send_message("لا يمكن إزالة صاحب التذكرة.", ephemeral=True)
                await interaction.channel.set_permissions(member, overwrite=None, reason=f"إزالة عضو من التذكرة #{ticket['id']}")
                await interaction.response.send_message(f"تمت إزالة {member.mention} من التذكرة.", ephemeral=True)


class OpenTicketButton(discord.ui.Button):
    def __init__(self, cog: "TicketManager", panel_id: int, option_index: int, option: dict[str, Any]):
        style_name = str(option.get("button_style") or "primary").lower()
        super().__init__(
            label=str(option.get("name") or "فتح تذكرة")[:80],
            emoji=str(option.get("emoji") or "🎫")[:20],
            style=BUTTON_STYLES.get(style_name, discord.ButtonStyle.primary),
            custom_id=f"ader:ticket:open:{panel_id}:{option_index}",
            row=min(4, option_index // 5),
        )
        self.cog = cog
        self.panel_id = panel_id
        self.option_index = option_index

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket_from_panel(interaction, self.panel_id, self.option_index)


class OpenTicketSelect(discord.ui.Select):
    def __init__(self, cog: "TicketManager", panel: dict[str, Any]):
        options: list[discord.SelectOption] = []
        for index, item in enumerate(panel.get("options", [])[:MAX_OPTIONS]):
            if not as_bool(item.get("enabled"), True):
                continue
            options.append(discord.SelectOption(
                label=str(item.get("name") or "فتح تذكرة")[:100],
                description=str(item.get("description") or "فتح تذكرة")[:100],
                emoji=str(item.get("emoji") or "🎫")[:20],
                value=str(index),
            ))
        if not options:
            options = [discord.SelectOption(label="فتح تذكرة", value="0", emoji="🎫")]
        super().__init__(
            placeholder=str((panel.get("settings") or {}).get("select_placeholder") or "اختر نوع التذكرة")[:150],
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"ader:ticket:select:{panel['id']}",
        )
        self.cog = cog
        self.panel_id = int(panel["id"])

    async def callback(self, interaction: discord.Interaction):
        await self.cog.create_ticket_from_panel(interaction, self.panel_id, int(self.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "TicketManager", panel: dict[str, Any]):
        super().__init__(timeout=None)
        options = [x for x in (panel.get("options") or []) if as_bool(x.get("enabled"), True)][:MAX_OPTIONS]
        mode = str(panel.get("mode") or "buttons").lower()
        if mode == "select":
            self.add_item(OpenTicketSelect(cog, panel))
        else:
            for index, item in enumerate(options):
                self.add_item(OpenTicketButton(cog, int(panel["id"]), index, item))


class TicketActionView(discord.ui.View):
    def __init__(self, cog: "TicketManager", channel_id: int, closed: bool = False, claimed: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.closed = closed
        if closed:
            self.add_item(TicketActionSelect(cog, channel_id, closed=True))
            return
        claim_button = TicketClaimButton(cog, channel_id)
        claim_button.disabled = claimed
        self.add_item(claim_button)
        unclaim_button = TicketUnclaimButton(cog, channel_id)
        unclaim_button.disabled = not claimed
        self.add_item(unclaim_button)
        self.add_item(TicketSupportRequestButton(cog, channel_id))
        self.add_item(TicketActionSelect(cog, channel_id, closed=False))


class BaseTicketButton(discord.ui.Button):
    def __init__(self, *, label: str, emoji: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, emoji=emoji, style=style, custom_id=custom_id, row=0)


class TicketClaimButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="استلام", emoji="🙋", style=discord.ButtonStyle.success, custom_id=f"ader:ticket:claim:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("هذه التذكرة غير موجودة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
        settings = await self.cog.settings_for_ticket(ticket)
        if not as_bool(settings.get("claim_enabled"), True):
            return await interaction.response.send_message("ميزة تولّي التذاكر معطلة حالياً.", ephemeral=True)
        if int(ticket["user_id"] or 0) == interaction.user.id:
            return await interaction.response.send_message("لا يمكن لصاحب التذكرة تولّي تذكرته.", ephemeral=True)
        claimed = as_int(ticket.get("claimed_by"))
        if claimed:
            return await interaction.response.send_message(
                "هذه التذكرة متولّاة بالفعل. استخدم زر إلغاء الاستلام إذا كنت أنت المستلم.",
                ephemeral=True,
            )
        await self.cog.db.update_ticket(ticket["id"], {"claimed_by": interaction.user.id})
        await self.cog.write_ticket_data(ticket, {"claimed_by": interaction.user.id})
        await interaction.response.send_message(f"تم استلام التذكرة بواسطة {interaction.user.mention}.")
        try:
            await interaction.message.edit(view=TicketActionView(self.cog, self.channel_id, claimed=True))
        except (discord.HTTPException, AttributeError):
            pass
        await self.cog.log_event(ticket, "claim", {"staff_id": interaction.user.id})


class TicketCloseButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="إغلاق", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:close:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("هذه التذكرة غير موجودة.", ephemeral=True)
        settings = await self.cog.settings_for_ticket(ticket)
        owner = int(ticket["user_id"] or 0) == interaction.user.id
        if owner and not as_bool(settings.get("allow_user_close"), True):
            return await interaction.response.send_message("لا تملك صلاحية إغلاق التذكرة وفق إعدادات السيرفر.", ephemeral=True)
        if not owner and not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("يمكن لصاحب التذكرة أو فريق الدعم إغلاقها.", ephemeral=True)
        await interaction.response.send_modal(CloseTicketModal(self.cog, self.channel_id))


class TicketLockButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="قفل", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:lock:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("هذه التذكرة غير موجودة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
        settings = await self.cog.settings_for_ticket(ticket)
        if not as_bool(settings.get("allow_lock"), True):
            return await interaction.response.send_message("ميزة قفل التذكرة معطلة.", ephemeral=True)
        data = self.cog.ticket_data(ticket)
        locked = as_bool(data.get("locked"), False)
        await self.cog.set_locked(ticket, not locked)
        await interaction.response.send_message("تم قفل التذكرة." if not locked else "تم فتح التذكرة.", ephemeral=True)


class TicketMemberButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int, mode: str):
        text = "إضافة عضو" if mode == "add" else "إزالة عضو"
        super().__init__(label=text, emoji="👤" if mode == "add" else "➖", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:{mode}:{channel_id}")
        self.cog, self.channel_id, self.mode = cog, channel_id, mode

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("التذكرة غير موجودة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
        settings = await self.cog.settings_for_ticket(ticket)
        key = "allow_member_add" if self.mode == "add" else "allow_member_remove"
        if not as_bool(settings.get(key), True):
            return await interaction.response.send_message("هذه الميزة معطلة من إعدادات التذاكر.", ephemeral=True)
        await interaction.response.send_message(
            "اختر العضو الذي تريد إضافته." if self.mode == "add" else "اختر العضو الذي تريد إزالته.",
            view=AddMemberView(self.cog, self.channel_id, self.mode),
            ephemeral=True,
        )


class TicketRenameButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="إعادة تسمية", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:rename:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("التذكرة غير موجودة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
        settings = await self.cog.settings_for_ticket(ticket)
        if not as_bool(settings.get("allow_rename"), True):
            return await interaction.response.send_message("إعادة التسمية معطلة.", ephemeral=True)
        await interaction.response.send_modal(RenameTicketModal(self.cog, self.channel_id))


class TicketTranscriptButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="السجل", emoji="📄", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:transcript:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("التذكرة غير موجودة.", ephemeral=True)
        if int(ticket["user_id"] or 0) != interaction.user.id and not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("السجل متاح لصاحب التذكرة وفريق الدعم فقط.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            location = await self.cog.generate_transcript(ticket, interaction.channel)
            await interaction.followup.send(f"تم إنشاء سجل التذكرة: {location}", ephemeral=True)
        except Exception:
            await interaction.followup.send("تعذر إنشاء السجل حالياً.", ephemeral=True)


class TicketReopenButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="إعادة فتح", emoji="🔓", style=discord.ButtonStyle.success, custom_id=f"ader:ticket:reopen:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket or ticket["status"] != "closed":
            return await interaction.response.send_message("التذكرة ليست مغلقة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            settings = await self.cog.settings_for_ticket(ticket)
            if int(ticket["user_id"] or 0) != interaction.user.id or not as_bool(settings.get("allow_user_reopen"), False):
                return await interaction.response.send_message("إعادة فتح التذكرة متاحة لفريق الدعم فقط.", ephemeral=True)
        await self.cog.reopen_ticket(interaction, ticket)


class TicketDeleteButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="حذف نهائياً", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id=f"ader:ticket:delete:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("التذكرة غير موجودة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("حذف التذاكر متاح لفريق الدعم فقط.", ephemeral=True)
        await self.cog.delete_ticket(interaction, ticket)


class TicketPurchaseButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="الشراء", emoji="🛒", style=discord.ButtonStyle.primary, custom_id=f"ader:ticket:purchase:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        purchase = self.cog.bot.get_cog("TicketPurchase")
        if purchase is None:
            return await interaction.response.send_message("نظام الشراء داخل التذكرة غير متوفر حالياً.", ephemeral=True)
        await purchase.open_purchase(interaction, self.channel_id)


class TicketUnclaimButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="إلغاء الاستلام", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id=f"ader:ticket:unclaim:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket:
            return await interaction.response.send_message("هذه التذكرة غير موجودة.", ephemeral=True)
        if not await self.cog.is_staff(interaction, ticket):
            return await interaction.response.send_message("هذه العملية متاحة لفريق الدعم فقط.", ephemeral=True)
        if as_int(ticket.get("claimed_by")) != interaction.user.id:
            return await interaction.response.send_message("لا يمكنك إلغاء استلام تذكرة لم تستلمها أنت.", ephemeral=True)
        await self.cog.db.update_ticket(ticket["id"], {"claimed_by": None})
        await self.cog.write_ticket_data(ticket, {"claimed_by": None})
        await interaction.response.send_message("تم إلغاء استلامك للتذكرة.", ephemeral=True)
        try:
            await interaction.message.edit(view=TicketActionView(self.cog, self.channel_id, claimed=False))
        except (discord.HTTPException, AttributeError):
            pass
        await self.cog.log_event(ticket, "unclaim", {"staff_id": interaction.user.id})


class TicketSupportRequestButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="طلب الدعم", emoji="🆘", style=discord.ButtonStyle.primary, custom_id=f"ader:ticket:support:{channel_id}")
        self.cog, self.channel_id = cog, channel_id

    async def callback(self, interaction: discord.Interaction):
        ticket = await self.cog.get_ticket_by_channel(self.channel_id)
        if not ticket or ticket["status"] not in {"open", "locked"}:
            return await interaction.response.send_message("لا يمكن طلب الدعم من تذكرة مغلقة أو غير موجودة.", ephemeral=True)
        if int(ticket.get("user_id") or 0) != interaction.user.id:
            return await interaction.response.send_message("زر طلب الدعم مخصص لصاحب التذكرة.", ephemeral=True)

        data = self.cog.ticket_data(ticket)
        now = time.time()
        last_request = float(data.get("support_requested_at") or 0)
        wait_seconds = 300 - int(now - last_request)
        if wait_seconds > 0:
            return await interaction.response.send_message(
                f"لقد أرسلت طلب دعم مؤخراً. حاول بعد {wait_seconds} ثانية.",
                ephemeral=True,
            )

        settings = await self.cog.settings_for_ticket(ticket)
        role_ids = data.get("support_role_ids") or ([data.get("support_role_id")] if data.get("support_role_id") else []) or settings.get("default_support_role_ids") or ([settings.get("default_support_role_id")] if settings.get("default_support_role_id") else [])
        roles = [interaction.guild.get_role(as_int(value) or 0) for value in role_ids]
        roles = [role for role in roles if role]
        await self.cog.write_ticket_data(ticket, {"support_requested_at": now})
        embed = discord.Embed(
            title="طلب دعم جديد",
            description=f"صاحب التذكرة {interaction.user.mention} يطلب حضور فريق الدعم.",
            color=EmbedColor.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        content = " ".join(role.mention for role in roles) or "فريق الدعم، يوجد طلب مساعدة جديد."
        await interaction.channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=bool(roles), users=False, everyone=False))
        await interaction.response.send_message("تم إرسال طلب الدعم إلى الفريق.", ephemeral=True)
        await self.cog.log_event(ticket, "support_requested", {"requested_by": interaction.user.id, "support_role_ids": [role.id for role in roles]})


class TicketActionSelect(discord.ui.Select):
    def __init__(self, cog: "TicketManager", channel_id: int, closed: bool = False):
        self.cog = cog
        self.channel_id = channel_id
        self.closed = closed
        if closed:
            options = [
                discord.SelectOption(label="إعادة فتح التذكرة", value="reopen", emoji="🔓"),
                discord.SelectOption(label="إنشاء السجل", value="transcript", emoji="📄"),
                discord.SelectOption(label="حذف التذكرة نهائياً", value="delete", emoji="🗑️"),
            ]
            placeholder = "إجراءات التذكرة المغلقة"
        else:
            options = [
                discord.SelectOption(label="إغلاق التذكرة", value="close", emoji="🔒"),
                discord.SelectOption(label="إضافة عضو", value="add_member", emoji="➕"),
                discord.SelectOption(label="إزالة عضو", value="remove_member", emoji="➖"),
                discord.SelectOption(label="إعادة تسمية التذكرة", value="rename", emoji="✏️"),
                discord.SelectOption(label="إنشاء سجل التذكرة", value="transcript", emoji="📄"),
            ]
            if cog.bot.get_cog("TicketPurchase") is not None:
                options.append(discord.SelectOption(label="الشراء داخل التذكرة", value="purchase", emoji="🛒"))
            placeholder = "اختر إجراءً من إجراءات التذكرة"
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"ader:ticket:actions:{channel_id}:{'closed' if closed else 'open'}",
            row=0 if closed else 1,
        )

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        handlers = {
            "close": TicketCloseButton(self.cog, self.channel_id),
            "add_member": TicketMemberButton(self.cog, self.channel_id, "add"),
            "remove_member": TicketMemberButton(self.cog, self.channel_id, "remove"),
            "rename": TicketRenameButton(self.cog, self.channel_id),
            "transcript": TicketTranscriptButton(self.cog, self.channel_id),
            "purchase": TicketPurchaseButton(self.cog, self.channel_id),
            "reopen": TicketReopenButton(self.cog, self.channel_id),
            "delete": TicketDeleteButton(self.cog, self.channel_id),
        }
        handler = handlers.get(action)
        if handler is None:
            return await interaction.response.send_message("هذا الإجراء غير متاح.", ephemeral=True)
        await handler.callback(interaction)


class TicketManager(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self._create_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._started = False

    async def cog_load(self):
        for panel in await self.db.get_all_ticket_panels():
            if panel.get("message_id"):
                try:
                    self.bot.add_view(TicketPanelView(self, panel), message_id=int(panel["message_id"]))
                except Exception:
                    pass
        asyncio.create_task(self._restore_ticket_views())

    async def _restore_ticket_views(self):
        await self.bot.wait_until_ready()
        rows = await self.db.fetchall(
            "SELECT channel_id,status,user_id,claimed_by FROM tickets "
            "WHERE channel_id IS NOT NULL AND status IN ('open','locked','closed')"
        )
        for row in rows:
            channel_id = int(row["channel_id"])
            try:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue
                closed = str(row["status"]) == "closed"
                if closed and row["user_id"]:
                    owner_id = int(row["user_id"])
                    owner_target = channel.guild.get_member(owner_id) or discord.Object(id=owner_id)
                    try:
                        await channel.set_permissions(
                            owner_target,
                            view_channel=False,
                            send_messages=False,
                            read_message_history=False,
                            reason="تطبيق خصوصية التذكرة المغلقة",
                        )
                    except discord.HTTPException:
                        pass

                view = TicketActionView(
                    self,
                    channel_id,
                    closed=closed,
                    claimed=bool(row["claimed_by"]),
                )
                self.bot.add_view(view)

                # Replace old ticket action buttons with the new menu layout.
                bot_user_id = self.bot.user.id if self.bot.user else 0
                if bot_user_id:
                    try:
                        async for message in channel.history(limit=30, oldest_first=False):
                            if message.author.id != bot_user_id or not message.components:
                                continue
                            custom_ids = [
                                getattr(component, "custom_id", None)
                                for action_row in message.components
                                for component in getattr(action_row, "children", ())
                            ]
                            legacy_action_prefixes = (
                                "ader:ticket:claim:",
                                "ader:ticket:close:",
                                "ader:ticket:lock:",
                                "ader:ticket:add:",
                                "ader:ticket:remove:",
                                "ader:ticket:rename:",
                                "ader:ticket:transcript:",
                                "ader:ticket:reopen:",
                                "ader:ticket:delete:",
                                "ader:ticket:actions:",
                            )
                            if any(
                                str(custom_id or "").startswith(legacy_action_prefixes)
                                for custom_id in custom_ids
                            ):
                                await message.edit(view=view)
                                break
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

    def ticket_data(self, ticket: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(ticket.get("data") or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    async def write_ticket_data(self, ticket: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        value = self.ticket_data(ticket)
        value.update(patch)
        await self.db.update_ticket(ticket["id"], {"data": json.dumps(value, ensure_ascii=False)})
        ticket["data"] = json.dumps(value, ensure_ascii=False)
        return value

    async def settings_for_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        guild_id = int(ticket["guild_id"])
        settings = merge_settings(await self.db.get_ticket_settings(guild_id))
        data = self.ticket_data(ticket)
        override = data.get("settings")
        if isinstance(override, dict):
            settings.update(override)
        return settings

    async def is_staff_member(self, member: discord.Member, ticket: dict[str, Any] | None = None) -> bool:
        if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
            return True
        guild = member.guild
        data = self.ticket_data(ticket or {})
        if data.get("support_roles_customized"):
            role_ids = data.get("support_role_ids") or []
        else:
            role_ids = data.get("support_role_ids") or ([data.get("support_role_id")] if data.get("support_role_id") else [])
            if not role_ids:
                settings = merge_settings(await self.db.get_ticket_settings(guild.id))
                role_ids = settings.get("default_support_role_ids") or ([settings.get("default_support_role_id")] if settings.get("default_support_role_id") else [])
        return any(role and role in member.roles for role in (guild.get_role(as_int(value) or 0) for value in role_ids))

    async def is_staff(self, interaction: discord.Interaction, ticket: dict[str, Any] | None = None) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            return False
        return await self.is_staff_member(member, ticket)

    async def get_ticket_by_channel(self, channel_id: int):
        row = await self.db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status IN ('open','locked','closed') LIMIT 1", (channel_id,))
        return dict(row) if row else None

    async def get_open_tickets_for_user(self, guild: discord.Guild, user_id: int) -> list[dict[str, Any]]:
        """Return active tickets only when their Discord text channels still exist."""
        rows = await self.db.fetchall(
            "SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status IN ('open','locked') ORDER BY id DESC",
            (guild.id, user_id),
        )
        active: list[dict[str, Any]] = []
        for row in rows:
            ticket = dict(row)
            channel_id = as_int(ticket.get("channel_id"))
            channel = guild.get_channel(channel_id) if channel_id else None

            if channel is None and channel_id:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    channel = None
                except (discord.Forbidden, discord.HTTPException):
                    # A temporary/API permission failure is not proof the room was deleted.
                    active.append(ticket)
                    continue

            if isinstance(channel, discord.TextChannel) and channel.guild.id == guild.id:
                active.append(ticket)
                continue

            # Clean up stale rows so a deleted channel does not block ticket creation.
            await self.db.execute(
                "UPDATE tickets SET status='deleted', closed_at=COALESCE(closed_at, ?) "
                "WHERE id=? AND status IN ('open','locked')",
                (time.time(), int(ticket["id"])),
            )
        return active

    def option_for(self, panel: dict[str, Any], index: int) -> dict[str, Any]:
        options = panel.get("options") or []
        try:
            item = options[index]
        except (IndexError, TypeError):
            item = {}
        return item if isinstance(item, dict) else {}

    @staticmethod
    def channel_template(template: str, *, ticket_id: int, member: discord.Member, type_name: str) -> str:
        values = {
            "number": str(ticket_id),
            "id": str(member.id),
            "user": clean_name(member.name, "member")[:40],
            "username": clean_name(member.name, "member")[:40],
            "type": clean_name(type_name, "ticket")[:30],
        }
        text = str(template or "ticket-{number}-{user}")
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        return clean_name(text, f"ticket-{ticket_id}")

    async def create_ticket_from_panel(self, interaction: discord.Interaction, panel_id: int, option_index: int):
        if not interaction.guild:
            return await interaction.response.send_message("لا يمكن فتح التذاكر خارج السيرفر.", ephemeral=True)
        guild = interaction.guild
        settings = merge_settings(await self.db.get_ticket_settings(guild.id))
        if not as_bool(settings.get("enabled"), True):
            return await interaction.response.send_message("نظام التذاكر معطل حالياً.", ephemeral=True)

        panel = await self.db.get_ticket_panel(panel_id)
        if not panel or int(panel["guild_id"]) != guild.id:
            return await interaction.response.send_message("لوحة التذاكر غير صالحة.", ephemeral=True)
        item = self.option_for(panel, option_index)
        if item.get("enabled") is False:
            return await interaction.response.send_message("هذا النوع من التذاكر غير متاح حالياً.", ephemeral=True)

        category_id = as_int(item.get("category_id")) or as_int(panel.get("category_id")) or as_int(settings.get("default_category_id"))
        category = guild.get_channel(category_id or 0)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("لم يتم ضبط فئة التذاكر بشكل صحيح. اطلب من الإدارة ضبطها من لوحة التحكم.", ephemeral=True)

        if item.get("support_roles_customized"):
            support_role_ids = item.get("support_role_ids") or []
        elif item.get("support_role_ids") or item.get("support_role_id"):
            support_role_ids = item.get("support_role_ids") or [item.get("support_role_id")]
        elif panel.get("support_roles_customized"):
            support_role_ids = panel.get("support_role_ids") or []
        elif panel.get("support_role_id") and not panel.get("support_role_ids"):
            # Keep older panels working when only the original single-role column is populated.
            support_role_ids = [panel.get("support_role_id")]
        else:
            support_role_ids = settings.get("default_support_role_ids") or ([settings.get("default_support_role_id")] if settings.get("default_support_role_id") else [])
        support_role_ids = list(dict.fromkeys(int(value) for value in support_role_ids if as_int(value) and guild.get_role(int(value))))
        support_roles = [guild.get_role(role_id) for role_id in support_role_ids]
        support_roles = [role for role in support_roles if role]
        support_role_id = support_role_ids[0] if support_role_ids else None
        limit = as_int(item.get("max_open")) or int(settings.get("max_open_per_user", 1))
        limit = max(1, min(MAX_OPEN_PER_USER, limit))
        open_tickets = await self.get_open_tickets_for_user(guild, interaction.user.id)
        current_count = len(open_tickets)
        if current_count >= limit:
            row = open_tickets[0] if open_tickets else None
            channel_ref = f"<#{row['channel_id']}>" if row and row.get("channel_id") else "التذكرة الحالية"
            return await interaction.response.send_message(f"لقد بلغت الحد الأقصى للتذاكر المفتوحة ({limit}). {channel_ref}", ephemeral=True)

        lock = self._create_locks.setdefault((guild.id, interaction.user.id), asyncio.Lock())
        async with lock:
            open_tickets = await self.get_open_tickets_for_user(guild, interaction.user.id)
            if len(open_tickets) >= limit:
                row = open_tickets[0] if open_tickets else None
                channel_ref = f"<#{row['channel_id']}>" if row and row.get("channel_id") else "التذكرة الحالية"
                return await interaction.response.send_message(
                    f"لقد بلغت الحد الأقصى للتذاكر المفتوحة ({limit}). {channel_ref}",
                    ephemeral=True,
                )
            await interaction.response.defer(ephemeral=True)
            channel = None
            try:
                provisional_id = int((await self.db.fetchone("SELECT COALESCE(MAX(id),0)+1 AS next_id FROM tickets"))["next_id"])
                name = self.channel_template(item.get("ticket_name") or panel.get("channel_name_template") or settings.get("channel_name_template"), ticket_id=provisional_id, member=interaction.user, type_name=str(item.get("name") or "دعم"))
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
                }
                me = guild.me
                if me:
                    overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, attach_files=True)
                for support_role in support_roles:
                    overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
                channel = await category.create_text_channel(name=name, overwrites=overwrites, reason=f"دعم Ader • {interaction.user}")
                ticket_data = {
                    "type": str(item.get("name") or "دعم"),
                    "type_description": str(item.get("description") or panel.get("ticket_description") or "يرجى شرح المشكلة بالتفصيل."),
                    "support_role_id": support_role_id,
                    "support_role_ids": support_role_ids,
                    "support_roles_customized": bool(item.get("support_roles_customized") or panel.get("support_roles_customized")),
                    "panel_id": int(panel["id"]),
                    "option_index": option_index,
                    "priority": str(item.get("priority") or "normal"),
                    "locked": False,
                    "claimed_by": None,
                    "settings": {
                        "allow_user_close": as_bool(settings.get("allow_user_close"), True),
                        "allow_user_reopen": as_bool(settings.get("allow_user_reopen"), False),
                    },
                }
                ticket_id = int(await self.db.create_ticket({"guild_id": guild.id, "user_id": interaction.user.id, "channel_id": channel.id, "status": "open", "data": json.dumps(ticket_data, ensure_ascii=False)}))
                # The ticket id may differ from the provisional name; retain a predictable id variable in the stored data.
                ticket_data["ticket_id"] = ticket_id
                await self.write_ticket_data({"id": ticket_id, "data": json.dumps(ticket_data, ensure_ascii=False)}, ticket_data)

                color = valid_color(item.get("color"), valid_color(panel.get("settings", {}).get("color"), 0x5865F2))
                embed = discord.Embed(
                    title=str(item.get("title") or item.get("name") or "تذكرة دعم")[:256],
                    description=f"مرحباً {interaction.user.mention}،\n\n{item.get('description') or panel.get('ticket_description') or 'يرجى شرح المشكلة بالتفصيل.'}",
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                if item.get("image_url") or panel.get("settings", {}).get("ticket_image_url"):
                    image = valid_url(item.get("image_url") or panel.get("settings", {}).get("ticket_image_url"))
                    if image:
                        embed.set_image(url=image)
                footer = str(item.get("footer") or panel.get("settings", {}).get("ticket_footer") or "دعم Ader")
                embed.set_footer(text=footer[:2048])
                mentions = discord.AllowedMentions(users=True, roles=bool(support_roles))
                content = interaction.user.mention
                if support_roles:
                    content += " " + " ".join(role.mention for role in support_roles)
                await channel.send(content=content, embed=embed, view=TicketActionView(self, channel.id), allowed_mentions=mentions)
                self.bot.add_view(TicketActionView(self, channel.id), message_id=None)
                await self.log_event({"id": ticket_id, "guild_id": guild.id, "user_id": interaction.user.id, "channel_id": channel.id, "data": json.dumps(ticket_data)}, "created", {"type": ticket_data["type"], "staff_role_id": support_role_id})
                await interaction.followup.send(f"تم إنشاء التذكرة بنجاح: {channel.mention} • رقم #{ticket_id}", ephemeral=True)
            except Exception:
                if channel:
                    try:
                        await channel.delete(reason="التراجع عن إنشاء تذكرة بعد فشل العملية")
                    except discord.HTTPException:
                        pass
                await interaction.followup.send("تعذر إنشاء التذكرة. تحقق من صلاحيات البوت وإعدادات الفئة ثم حاول مرة أخرى.", ephemeral=True)

    async def _close_ticket(self, interaction: discord.Interaction, channel_id: int, reason: str = ""):
        ticket = await self.get_ticket_by_channel(channel_id)
        if not ticket:
            return await interaction.response.send_message("هذه التذكرة غير موجودة.", ephemeral=True)
        if ticket["status"] == "closed":
            return await interaction.response.send_message("التذكرة مغلقة بالفعل.", ephemeral=True)
        if int(ticket["user_id"] or 0) != interaction.user.id and not await self.is_staff(interaction, ticket):
            return await interaction.response.send_message("لا يملك صلاحية إغلاق هذه التذكرة.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        owner_id = int(ticket["user_id"])
        owner_target = interaction.guild.get_member(owner_id) or discord.Object(id=owner_id)
        try:
            # Removing view_channel is the privacy boundary for a closed ticket.
            await interaction.channel.set_permissions(
                owner_target,
                view_channel=False,
                send_messages=False,
                read_message_history=False,
                reason=f"إغلاق التذكرة #{ticket['id']}",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send(
                "تعذر إخفاء التذكرة عن صاحبها، لذلك لم يتم إغلاقها. تحقق من صلاحية Manage Channels لدى البوت.",
                ephemeral=True,
            )

        await self.db.update_ticket(ticket["id"], {
            "status": "closed",
            "closed_at": time.time(),
            "data": json.dumps({
                **self.ticket_data(ticket),
                "close_reason": reason[:1000],
                "closed_by": interaction.user.id,
                "locked": True,
            }, ensure_ascii=False),
        })
        try:
            await interaction.channel.edit(name=clean_name("closed-" + interaction.channel.name, f"closed-ticket-{ticket['id']}"))
        except discord.HTTPException:
            pass

        transcript_note = None
        settings = await self.settings_for_ticket(ticket)
        if as_int(settings.get("transcript_channel_id")):
            transcript_note = await self.send_transcript_to_log(ticket, interaction.channel)
        await self.log_event(ticket, "closed", {"reason": reason[:1000], "closed_by": interaction.user.id, "transcript": transcript_note})
        try:
            await interaction.message.edit(content="تم إغلاق التذكرة. يمكن لفريق الدعم إعادة فتحها أو حذفها.", embed=interaction.message.embeds[0] if interaction.message.embeds else None, view=TicketActionView(self, channel_id, closed=True))
        except Exception:
            await interaction.channel.send("تم إغلاق التذكرة. يمكن لفريق الدعم إعادة فتحها أو حذفها.", view=TicketActionView(self, channel_id, closed=True))

        if as_bool(settings.get("rating_enabled"), True):
            try:
                owner = interaction.guild.get_member(int(ticket["user_id"]))
                if owner:
                    await owner.send(
                        embed=discord.Embed(
                            title="تقييم تجربة الدعم",
                            description=f"أُغلقت تذكرتك رقم #{ticket['id']}.\nاختر تقييماً من نجمة إلى خمس نجوم، ويمكنك إضافة تعليق.",
                            color=valid_color((await self.db.get_ticket_panel(int(self.ticket_data(ticket).get("panel_id") or 0)) or {}).get("settings", {}).get("color"), 0x5865F2),
                        ),
                        view=RatingView(self, int(ticket["id"])),
                    )
            except Exception:
                pass
        delete_after = as_int(settings.get("delete_after_close_seconds"), 0) or 0
        if delete_after > 0 and not as_bool(settings.get("keep_closed"), True):
            await asyncio.sleep(delete_after)
            try:
                await interaction.channel.delete(reason=f"حذف تلقائي للتذكرة المغلقة #{ticket['id']}")
            except discord.HTTPException:
                pass
        await interaction.followup.send("تم إغلاق التذكرة بنجاح.", ephemeral=True)

    async def set_locked(self, ticket: dict[str, Any], locked: bool):
        channel = self.bot.get_channel(int(ticket["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return False
        owner_id = int(ticket["user_id"])
        owner_target = channel.guild.get_member(owner_id) or discord.Object(id=owner_id)
        await channel.set_permissions(
            owner_target,
            send_messages=not locked,
            view_channel=True,
            read_message_history=True,
            reason=f"قفل التذكرة #{ticket['id']}" if locked else f"فتح التذكرة #{ticket['id']}",
        )
        await self.write_ticket_data(ticket, {"locked": locked})
        await self.db.update_ticket(ticket["id"], {"status": "locked" if locked else "open", "data": json.dumps(self.ticket_data(ticket), ensure_ascii=False)})
        return True

    async def reopen_ticket(self, interaction: discord.Interaction, ticket: dict[str, Any]):
        channel = self.bot.get_channel(int(ticket["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("قناة التذكرة غير موجودة.", ephemeral=True)
        owner_id = int(ticket["user_id"])
        owner_target = channel.guild.get_member(owner_id) or discord.Object(id=owner_id)
        await channel.set_permissions(
            owner_target,
            send_messages=True,
            view_channel=True,
            read_message_history=True,
            attach_files=True,
            reason=f"إعادة فتح التذكرة #{ticket['id']}",
        )
        try:
            if channel.name.startswith("closed-"):
                await channel.edit(name=channel.name[7:], reason=f"إعادة فتح التذكرة #{ticket['id']}")
        except discord.HTTPException:
            pass
        data = self.ticket_data(ticket)
        data.update({"locked": False, "reopened_by": interaction.user.id, "reopened_at": time.time()})
        await self.db.update_ticket(ticket["id"], {"status": "open", "closed_at": None, "data": json.dumps(data, ensure_ascii=False)})
        try:
            await interaction.message.edit(view=TicketActionView(self, int(channel.id), closed=False, claimed=bool(as_int(ticket.get("claimed_by")))))
        except Exception:
            pass
        await interaction.response.send_message("تمت إعادة فتح التذكرة.", ephemeral=True)
        await self.log_event(ticket, "reopened", {"by": interaction.user.id})

    async def delete_ticket(self, interaction: discord.Interaction, ticket: dict[str, Any]):
        await interaction.response.send_message("سيتم حذف التذكرة نهائياً بعد إنشاء السجل إن كان مفعلاً.", ephemeral=True)
        settings = await self.settings_for_ticket(ticket)
        if as_int(settings.get("transcript_channel_id")):
            try:
                await self.send_transcript_to_log(ticket, interaction.channel)
            except Exception:
                pass
        await self.db.update_ticket(ticket["id"], {"status": "deleted", "closed_at": ticket.get("closed_at") or time.time()})
        await self.log_event(ticket, "deleted", {"by": interaction.user.id})
        await asyncio.sleep(1)
        try:
            await interaction.channel.delete(reason=f"حذف التذكرة #{ticket['id']} بواسطة {interaction.user}")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        raw = (message.content or "").strip()
        lowered = raw.lower()
        command = None
        new_name = ""
        if lowered == "استلام":
            command = "claim"
        elif lowered == "الغاء استلام":
            command = "unclaim"
        elif lowered == "$close":
            command = "close"
        elif lowered == "$delete":
            command = "delete"
        elif lowered.startswith("$re "):
            command = "rename"
            new_name = raw[3:].strip()
        if command is None or (command == "rename" and not new_name):
            return

        ticket = await self.get_ticket_by_channel(message.channel.id)
        if not ticket or ticket.get("status") not in {"open", "locked", "closed"}:
            return
        if not await self.is_staff_member(message.author, ticket):
            try:
                await message.reply("هذه الاختصارات مخصصة لفريق الإدارة والدعم فقط.", delete_after=5, mention_author=False)
            except discord.HTTPException:
                pass
            return

        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        if command == "rename":
            clean = clean_name(new_name, f"ticket-{ticket['id']}")
            try:
                await channel.edit(name=clean, reason=f"إعادة تسمية التذكرة #{ticket['id']} بواسطة {message.author}")
                await self.write_ticket_data(ticket, {"renamed_to": clean, "renamed_by": message.author.id})
            except discord.HTTPException:
                await channel.send("تعذر تغيير اسم التذكرة. تحقق من صلاحيات البوت.")
            return

        if command == "claim":
            if ticket.get("status") == "closed":
                return
            claimed_by = as_int(ticket.get("claimed_by"))
            if claimed_by:
                await channel.send(f"التذكرة مستلمة بالفعل بواسطة <@{claimed_by}>.")
                return
            await self.db.update_ticket(ticket["id"], {"claimed_by": message.author.id})
            await self.write_ticket_data(ticket, {"claimed_by": message.author.id})
            await channel.send(f"تم استلام التذكرة بواسطة {message.author.mention}.")
            await self.log_event(ticket, "claim", {"staff_id": message.author.id, "source": "text_command"})
            return

        if command == "unclaim":
            if ticket.get("status") == "closed":
                return
            claimed_by = as_int(ticket.get("claimed_by"))
            if not claimed_by:
                await channel.send("لا يوجد إداري مستلم لهذه التذكرة حالياً.")
                return
            if claimed_by != message.author.id and not (message.author.guild_permissions.administrator or message.author.guild_permissions.manage_channels):
                await channel.send("إلغاء الاستلام متاح للمستلم أو لإدارة السيرفر.")
                return
            await self.db.update_ticket(ticket["id"], {"claimed_by": None})
            await self.write_ticket_data(ticket, {"claimed_by": None})
            await channel.send(f"تم إلغاء استلام التذكرة بواسطة {message.author.mention}.")
            await self.log_event(ticket, "unclaim", {"staff_id": message.author.id, "source": "text_command"})
            return

        if command == "close":
            if ticket.get("status") == "closed":
                await channel.send("التذكرة مغلقة بالفعل.")
                return
            owner_id = int(ticket["user_id"])
            owner_target = message.guild.get_member(owner_id) or discord.Object(id=owner_id)
            try:
                await channel.set_permissions(owner_target, view_channel=False, send_messages=False, read_message_history=False, reason=f"إغلاق التذكرة #{ticket['id']} بواسطة {message.author}")
            except (discord.Forbidden, discord.HTTPException):
                await channel.send("تعذر إخفاء التذكرة عن صاحبها، لذلك لم يتم إغلاقها. تحقق من صلاحية Manage Channels.")
                return
            closed_at = time.time()
            data = self.ticket_data(ticket)
            data.update({"close_reason": "تم الإغلاق بواسطة اختصار $close", "closed_by": message.author.id, "locked": True})
            await self.db.update_ticket(ticket["id"], {"status": "closed", "closed_at": closed_at, "data": json.dumps(data, ensure_ascii=False)})
            ticket["status"] = "closed"
            ticket["closed_at"] = closed_at
            ticket["data"] = json.dumps(data, ensure_ascii=False)
            try:
                if not channel.name.startswith("closed-"):
                    await channel.edit(name=clean_name("closed-" + channel.name, f"closed-ticket-{ticket['id']}"))
            except discord.HTTPException:
                pass
            settings = await self.settings_for_ticket(ticket)
            transcript_note = None
            if as_int(settings.get("transcript_channel_id")):
                try:
                    transcript_note = await self.send_transcript_to_log(ticket, channel)
                except Exception:
                    pass
            await self.log_event(ticket, "closed", {"reason": "تم الإغلاق بواسطة اختصار $close", "closed_by": message.author.id, "transcript": transcript_note, "source": "text_command"})
            await channel.send("تم إغلاق التذكرة. يمكن لفريق الدعم إعادة فتحها أو حذفها.", view=TicketActionView(self, channel.id, closed=True))
            if as_bool(settings.get("rating_enabled"), True):
                try:
                    owner = message.guild.get_member(owner_id)
                    if owner:
                        panel_id = int(self.ticket_data(ticket).get("panel_id") or 0)
                        panel = await self.db.get_ticket_panel(panel_id) if panel_id else None
                        await owner.send(embed=discord.Embed(
                            title="تقييم تجربة الدعم",
                            description=f"أُغلقت تذكرتك رقم #{ticket['id']}. اختر تقييماً من نجمة إلى خمس نجوم.",
                            color=valid_color((panel or {}).get("settings", {}).get("color"), 0x5865F2),
                        ), view=RatingView(self, int(ticket["id"])))
                except Exception:
                    pass
            delete_after = as_int(settings.get("delete_after_close_seconds"), 0) or 0
            if delete_after > 0 and not as_bool(settings.get("keep_closed"), True):
                async def delayed_delete():
                    await asyncio.sleep(delete_after)
                    try:
                        await channel.delete(reason=f"حذف تلقائي للتذكرة المغلقة #{ticket['id']}")
                    except discord.HTTPException:
                        pass
                asyncio.create_task(delayed_delete())
            return

        if command == "delete":
            settings = await self.settings_for_ticket(ticket)
            if as_int(settings.get("transcript_channel_id")):
                try:
                    await self.send_transcript_to_log(ticket, channel)
                except Exception:
                    pass
            await self.db.update_ticket(ticket["id"], {"status": "deleted", "closed_at": ticket.get("closed_at") or time.time()})
            await self.log_event(ticket, "deleted", {"by": message.author.id, "source": "text_command"})
            try:
                await channel.delete(reason=f"حذف التذكرة #{ticket['id']} بواسطة {message.author}")
            except discord.HTTPException:
                pass

    async def generate_transcript(self, ticket: dict[str, Any], channel: discord.TextChannel) -> str:
        lines = [
            f"سجل تذكرة Ader • #{ticket['id']}",
            f"Guild: {channel.guild.name} ({channel.guild.id})",
            f"Owner ID: {ticket['user_id']}",
            f"Status: {ticket['status']}",
            f"Created: {ticket.get('created_at')}",
            "",
        ]
        async for message in channel.history(limit=None, oldest_first=True):
            timestamp = message.created_at.isoformat()
            author = f"{message.author} ({message.author.id})"
            text = message.clean_content or ""
            attachments = " | ".join(a.url for a in message.attachments)
            lines.append(f"[{timestamp}] {author}: {text}")
            if attachments:
                lines.append(f"  Attachments: {attachments}")
        payload = "\n".join(lines).encode("utf-8", errors="replace")
        filename = f"ticket-{ticket['id']}-transcript.txt"
        file = discord.File(io.BytesIO(payload), filename=filename)
        settings = await self.settings_for_ticket(ticket)
        destination_id = as_int(settings.get("transcript_channel_id"))
        destination = channel.guild.get_channel(destination_id or 0) if destination_id else None
        if isinstance(destination, discord.TextChannel):
            await destination.send(content=f"سجل التذكرة رقم #{ticket['id']}", file=file, allowed_mentions=discord.AllowedMentions.none())
            return destination.mention
        await channel.send(content="تم إنشاء سجل التذكرة في القناة الحالية.", file=file, allowed_mentions=discord.AllowedMentions.none())
        return channel.mention

    async def send_transcript_to_log(self, ticket: dict[str, Any], channel: discord.TextChannel) -> str:
        return await self.generate_transcript(ticket, channel)

    async def save_rating(self, ticket: dict[str, Any], rating: int, comment: str):
        rating = max(1, min(5, int(rating)))
        staff_id = as_int(self.ticket_data(ticket).get("claimed_by"))
        await self.db.save_ticket_rating({"ticket_id": int(ticket["id"]), "guild_id": int(ticket["guild_id"]), "user_id": int(ticket["user_id"]), "staff_id": staff_id, "rating": rating, "comment": comment})

    EVENT_LABELS = {
        "created": "إنشاء التذكرة", "closed": "إغلاق التذكرة", "reopened": "إعادة فتح التذكرة",
        "claim": "استلام التذكرة", "unclaim": "إلغاء استلام التذكرة",
        "support_requested": "طلب دعم", "deleted": "حذف التذكرة", "rating": "تقييم التذكرة",
    }

    async def log_event(self, ticket: dict[str, Any], event: str, data: dict[str, Any]):
        guild = self.bot.get_guild(int(ticket["guild_id"]))
        if not guild:
            return
        payload = {
            "ticket_id": int(ticket["id"]),
            "user_id": int(ticket.get("user_id") or 0),
            "channel_id": int(ticket.get("channel_id") or 0),
            "event": event,
            **{str(k): v for k, v in data.items()},
        }
        try:
            await self.db.record_analytics(guild.id, f"ticket.{event}", payload)
        except Exception:
            pass

        settings = merge_settings(await self.db.get_ticket_settings(guild.id))
        destination_id = as_int(settings.get("log_channel_id"))
        channel = guild.get_channel(destination_id or 0) if destination_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=f"🎫 سجل التذاكر • {self.EVENT_LABELS.get(event, event)}",
            color=EmbedColor.PRIMARY,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="رقم التذكرة", value=f"#{ticket['id']}", inline=True)
        embed.add_field(name="صاحب التذكرة", value=f"<@{ticket['user_id']}>", inline=True)
        for key, value in list(data.items())[:8]:
            text = str(value)
            if len(text) > 900:
                text = text[:897] + "..."
            embed.add_field(name=str(key)[:256], value=text or "—", inline=False)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    def panel_embed(self, panel: dict[str, Any]) -> discord.Embed:
        settings = panel.get("settings") or {}
        color = valid_color(settings.get("color"), 0x5865F2)
        embed = discord.Embed(
            title=str(panel.get("title") or "الدعم الفني")[:256],
            description=str(panel.get("description") or "اختر نوع الطلب لفتح تذكرة.")[:4096],
            color=color,
        )
        if valid_url(panel.get("image_url")):
            embed.set_image(url=valid_url(panel.get("image_url")))
        if valid_url(settings.get("thumbnail_url")):
            embed.set_thumbnail(url=valid_url(settings.get("thumbnail_url")))
        footer = str(settings.get("footer") or "دعم Ader")
        if footer:
            embed.set_footer(text=footer[:2048])
        return embed

    def preview_embed(self, panel: dict[str, Any]) -> discord.Embed:
        embed = self.panel_embed(panel)
        embed.add_field(name="النمط", value="قائمة اختيار" if str(panel.get("mode")) == "select" else "أزرار", inline=True)
        embed.add_field(name="الأنواع", value=str(len(panel.get("options") or [])), inline=True)
        embed.add_field(name="الفئة", value=f"<#{panel['category_id']}>" if panel.get("category_id") else "غير محددة", inline=True)
        embed.add_field(name="قناة النشر", value=f"<#{panel['channel_id']}>" if panel.get("channel_id") else "غير محددة", inline=True)
        return embed

    async def publish_panel(self, panel_id: int) -> dict[str, Any]:
        panel = await self.db.get_ticket_panel(panel_id)
        if not panel:
            raise ValueError("لوحة التذاكر غير موجودة.")
        guild = self.bot.get_guild(int(panel["guild_id"]))
        if guild is None:
            raise ValueError("البوت غير متصل بالسيرفر حالياً.")
        channel = guild.get_channel(as_int(panel.get("channel_id")) or 0)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("قناة نشر لوحة التذاكر غير صالحة.")
        member = guild.me
        if member is None or not channel.permissions_for(member).send_messages:
            raise ValueError("البوت لا يملك صلاحية إرسال الرسائل في قناة اللوحة.")
        view = TicketPanelView(self, panel)
        message = None
        old_id = as_int(panel.get("message_id"))
        old_channel_id = as_int(panel.get("channel_id"))
        if old_id:
            # When the panel moves to another channel, remove the old published panel.
            if old_channel_id and old_channel_id != channel.id:
                old_channel = guild.get_channel(old_channel_id)
                if isinstance(old_channel, discord.TextChannel):
                    try:
                        await (await old_channel.fetch_message(old_id)).delete()
                    except (discord.NotFound, discord.HTTPException):
                        pass
            else:
                try:
                    message = await channel.fetch_message(old_id)
                    await message.edit(embed=self.panel_embed(panel), view=view)
                except (discord.NotFound, discord.HTTPException):
                    message = None
        if message is None:
            message = await channel.send(embed=self.panel_embed(panel), view=view)
        await self.db.update_ticket_panel(panel_id, {"message_id": message.id, "channel_id": channel.id})
        self.bot.add_view(view, message_id=message.id)
        return await self.db.get_ticket_panel(panel_id)

    @app_commands.command(name="ticket", description="إدارة نظام التذاكر من Discord")
    @is_admin()
    async def ticket_cmd(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("استخدم هذا الأمر داخل السيرفر.", ephemeral=True)
        from cogs.ticket_admin_ui import TicketManagementView
        embed = discord.Embed(
            title="مركز إدارة التذاكر",
            description=(
                "يمكنك إدارة نظام التذاكر بالكامل من داخل Discord دون الحاجة إلى فتح الداشبورد. "
                "الإعدادات واللوحات هنا مشتركة مع الداشبورد وتُحفظ في قاعدة البيانات نفسها."
            ),
            color=EmbedColor.PRIMARY,
        )
        await interaction.response.send_message(
            embed=embed,
            view=TicketManagementView(self, interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketManager(bot, bot.db, bot.config))
