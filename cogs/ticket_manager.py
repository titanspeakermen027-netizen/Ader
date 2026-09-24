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
    def __init__(self, cog: "TicketManager", channel_id: int, closed: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.closed = closed
        if closed:
            self.add_item(TicketReopenButton(cog, channel_id))
            self.add_item(TicketTranscriptButton(cog, channel_id))
            self.add_item(TicketDeleteButton(cog, channel_id))
            return
        self.add_item(TicketClaimButton(cog, channel_id))
        self.add_item(TicketCloseButton(cog, channel_id))
        self.add_item(TicketLockButton(cog, channel_id))
        self.add_item(TicketMemberButton(cog, channel_id, "add"))
        self.add_item(TicketRenameButton(cog, channel_id))
        self.add_item(TicketTranscriptButton(cog, channel_id))
        purchase = cog.bot.get_cog("TicketPurchase")
        if purchase is not None:
            self.add_item(TicketPurchaseButton(cog, channel_id))


class BaseTicketButton(discord.ui.Button):
    def __init__(self, *, label: str, emoji: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, emoji=emoji, style=style, custom_id=custom_id)


class TicketClaimButton(BaseTicketButton):
    def __init__(self, cog: "TicketManager", channel_id: int):
        super().__init__(label="تولّي التذكرة", emoji="🙋", style=discord.ButtonStyle.success, custom_id=f"ader:ticket:claim:{channel_id}")
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
            if claimed == interaction.user.id:
                await self.cog.write_ticket_data(ticket, {"claimed_by": None})
                await self.cog.db.update_ticket(ticket["id"], {"claimed_by": None})
                return await interaction.response.send_message("تم إلغاء تولّيك للتذكرة.", ephemeral=True)
            return await interaction.response.send_message("هذه التذكرة متولّاة بالفعل من عضو آخر في فريق الدعم.", ephemeral=True)
        await self.cog.db.update_ticket(ticket["id"], {"claimed_by": interaction.user.id})
        await self.cog.write_ticket_data(ticket, {"claimed_by": interaction.user.id})
        await interaction.response.send_message(f"تم تولّي التذكرة بواسطة {interaction.user.mention}.")
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
        rows = await self.db.fetchall(
            "SELECT channel_id,status FROM tickets WHERE channel_id IS NOT NULL AND status IN ('open','locked','closed')"
        )
        for row in rows:
            try:
                self.bot.add_view(TicketActionView(self, int(row["channel_id"]), str(row["status"]) == "closed"), message_id=None)
            except Exception:
                pass

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

    async def is_staff(self, interaction: discord.Interaction, ticket: dict[str, Any] | None = None) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
            return True
        data = self.ticket_data(ticket or {})
        support_role_id = as_int(data.get("support_role_id"))
        if support_role_id:
            role = interaction.guild.get_role(support_role_id)
            if role and role in member.roles:
                return True
        settings = merge_settings(await self.db.get_ticket_settings(member.guild.id))
        global_role = as_int(settings.get("default_support_role_id"))
        return bool(global_role and any(r.id == global_role for r in member.roles))

    async def get_ticket_by_channel(self, channel_id: int):
        row = await self.db.fetchone("SELECT * FROM tickets WHERE channel_id=? AND status IN ('open','locked','closed') LIMIT 1", (channel_id,))
        return dict(row) if row else None

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
        if not await self.db.is_server_premium(guild.id):
            return await interaction.response.send_message("نظام التذاكر الاحترافي متاح لسيرفرات Ader Premium فقط.", ephemeral=True)

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

        support_role_id = as_int(item.get("support_role_id")) or as_int(panel.get("support_role_id")) or as_int(settings.get("default_support_role_id"))
        support_role = guild.get_role(support_role_id) if support_role_id else None
        limit = as_int(item.get("max_open")) or int(settings.get("max_open_per_user", 1))
        limit = max(1, min(MAX_OPEN_PER_USER, limit))
        count_row = await self.db.fetchone("SELECT COUNT(*) AS n FROM tickets WHERE guild_id=? AND user_id=? AND status IN ('open','locked')", (guild.id, interaction.user.id))
        current_count = int(count_row["n"]) if count_row else 0
        if current_count >= limit:
            row = await self.db.fetchone("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status IN ('open','locked') ORDER BY id DESC LIMIT 1", (guild.id, interaction.user.id))
            channel_ref = f"<#{row['channel_id']}>" if row and row["channel_id"] else "التذكرة الحالية"
            return await interaction.response.send_message(f"لقد بلغت الحد الأقصى للتذاكر المفتوحة ({limit}). {channel_ref}", ephemeral=True)

        lock = self._create_locks.setdefault((guild.id, interaction.user.id), asyncio.Lock())
        async with lock:
            count_row = await self.db.fetchone("SELECT COUNT(*) AS n FROM tickets WHERE guild_id=? AND user_id=? AND status IN ('open','locked')", (guild.id, interaction.user.id))
            if int(count_row["n"] or 0) >= limit:
                return await interaction.response.send_message("لديك تذكرة مفتوحة بالفعل وفق الحد المحدد.", ephemeral=True)
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
                if support_role:
                    overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
                channel = await category.create_text_channel(name=name, overwrites=overwrites, reason=f"دعم Ader • {interaction.user}")
                ticket_data = {
                    "type": str(item.get("name") or "دعم"),
                    "type_description": str(item.get("description") or panel.get("ticket_description") or "يرجى شرح المشكلة بالتفصيل."),
                    "support_role_id": support_role_id,
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
                    description=f"مرحباً {interaction.user.mention}،

{item.get('description') or panel.get('ticket_description') or 'يرجى شرح المشكلة بالتفصيل.'}",
                    color=color,
                    timestamp=discord.utils.utcnow(),
                )
                if item.get("image_url") or panel.get("settings", {}).get("ticket_image_url"):
                    image = valid_url(item.get("image_url") or panel.get("settings", {}).get("ticket_image_url"))
                    if image:
                        embed.set_image(url=image)
                footer = str(item.get("footer") or panel.get("settings", {}).get("ticket_footer") or "دعم Ader")
                embed.set_footer(text=footer[:2048])
                mentions = discord.AllowedMentions(users=True, roles=bool(support_role))
                content = interaction.user.mention
                if support_role:
                    content += f" {support_role.mention}"
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
        await self.db.update_ticket(ticket["id"], {"status": "closed", "closed_at": time.time(), "data": json.dumps({**self.ticket_data(ticket), "close_reason": reason[:1000], "closed_by": interaction.user.id, "locked": True}, ensure_ascii=False)})
        try:
            owner = interaction.guild.get_member(int(ticket["user_id"]))
            if owner:
                await interaction.channel.set_permissions(owner, send_messages=False, view_channel=True, read_message_history=True, reason=f"إغلاق التذكرة #{ticket['id']}")
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
            await interaction.channel.send("تم إغلاق التذكرة. يمكن لفريق الدعم إعادة فتحها أو حذفها.", view=TicketActionView(self, channel_id))

        if as_bool(settings.get("rating_enabled"), True):
            try:
                owner = interaction.guild.get_member(int(ticket["user_id"]))
                if owner:
                    await owner.send(
                        embed=discord.Embed(
                            title="تقييم تجربة الدعم",
                            description=f"أُغلقت تذكرتك رقم #{ticket['id']}.
اختر تقييماً من نجمة إلى خمس نجوم، ويمكنك إضافة تعليق.",
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
        owner = channel.guild.get_member(int(ticket["user_id"]))
        if owner:
            await channel.set_permissions(owner, send_messages=not locked, view_channel=True, read_message_history=True, reason=f"قفل التذكرة #{ticket['id']}" if locked else f"فتح التذكرة #{ticket['id']}")
        await self.write_ticket_data(ticket, {"locked": locked})
        await self.db.update_ticket(ticket["id"], {"status": "locked" if locked else "open", "data": json.dumps(self.ticket_data(ticket), ensure_ascii=False)})
        return True

    async def reopen_ticket(self, interaction: discord.Interaction, ticket: dict[str, Any]):
        channel = self.bot.get_channel(int(ticket["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("قناة التذكرة غير موجودة.", ephemeral=True)
        owner = channel.guild.get_member(int(ticket["user_id"]))
        if owner:
            await channel.set_permissions(owner, send_messages=True, view_channel=True, read_message_history=True, attach_files=True, reason=f"إعادة فتح التذكرة #{ticket['id']}")
        try:
            if channel.name.startswith("closed-"):
                await channel.edit(name=channel.name[7:], reason=f"إعادة فتح التذكرة #{ticket['id']}")
        except discord.HTTPException:
            pass
        data = self.ticket_data(ticket)
        data.update({"locked": False, "reopened_by": interaction.user.id, "reopened_at": time.time()})
        await self.db.update_ticket(ticket["id"], {"status": "open", "closed_at": None, "data": json.dumps(data, ensure_ascii=False)})
        try:
            await interaction.message.edit(view=TicketActionView(self, int(channel.id), closed=False))
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

    async def generate_transcript(self, ticket: dict[str, Any], channel: discord.TextChannel) -> str:
        lines = [
            f"Ader Ticket Transcript • #{ticket['id']}",
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
        "claim": "تولّي التذكرة", "deleted": "حذف التذكرة", "rating": "تقييم التذكرة",
    }

    async def log_event(self, ticket: dict[str, Any], event: str, data: dict[str, Any]):
        guild = self.bot.get_guild(int(ticket["guild_id"]))
        if not guild:
            return
        settings = merge_settings(await self.db.get_ticket_settings(guild.id))
        destination_id = as_int(settings.get("log_channel_id"))
        channel = guild.get_channel(destination_id or 0) if destination_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(title=f"سجل التذاكر • {self.EVENT_LABELS.get(event, event)}", color=EmbedColor.PRIMARY, timestamp=discord.utils.utcnow())
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
        if old_id:
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

    def dashboard_url(self) -> str:
        return (str(__import__("os").getenv("DASHBOARD_FRONTEND_URL", "")).strip().rstrip("/") or
                str(__import__("os").getenv("DASHBOARD_PUBLIC_URL", "")).strip().rstrip("/"))

    @app_commands.command(name="ticket", description="إدارة نظام التذاكر الاحترافي")
    @is_admin()
    async def ticket_cmd(self, interaction: discord.Interaction):
        url = self.dashboard_url()
        embed = discord.Embed(
            title="نظام التذاكر الاحترافي",
            description="يمكنك إنشاء لوحات التذاكر وتخصيص الأنواع والقنوات والصلاحيات والسجلات من لوحة تحكم Ader.",
            color=EmbedColor.PRIMARY,
        )
        view = discord.ui.View(timeout=180)
        if url:
            view.add_item(discord.ui.Button(label="فتح لوحة التذاكر", style=discord.ButtonStyle.link, url=url))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketManager(bot, bot.db, bot.config))
