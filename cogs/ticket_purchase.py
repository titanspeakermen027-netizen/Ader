"""Ticket-based role purchasing with configurable payment methods.

The purchase flow lives inside an open Ader ticket:
1. Click ``شراء``.
2. Select the role.
3. Select a configured payment method button.
4. A transfer command is generated from the method template.
5. A trusted payment bot's configured success message is verified exactly
   enough to identify the pending order, then the role is granted.

All configuration and orders are stored in SQLite.
"""
from __future__ import annotations

import re
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


MAX_METHOD_BUTTONS = 25
ORDER_TIMEOUT = 24 * 60 * 60
ALLOWED_TEMPLATE_VARS = {
    "username",
    "amount",
    "user",
    "user_id",
    "recipient_id",
    "price",
    "tax",
    "role",
    "role_id",
    "method",
}


def _parse_emoji(value: str) -> str | discord.PartialEmoji:
    value = str(value or "🎫").strip()
    if value.startswith("<:") or value.startswith("<a:"):
        try:
            return discord.PartialEmoji.from_str(value)
        except Exception:
            return "🎫"
    return value[:20] or "🎫"


def _format_transfer(template: str, *, recipient_id: int, amount: int, price: int, tax: float, user_id: int, role: str, role_id: int, method: str, username: str) -> str:
    values = {
        "recipient_id": str(recipient_id),
        "amount": f"{amount:,}",
        "price": f"{price:,}",
        "tax": f"{tax:g}",
        "user_id": str(user_id),
        "role": role,
        "role_id": str(role_id),
        "method": method,
        "username": username,
        "user": f"<@{user_id}>",
    }
    return template.format(**values)


def _compile_success_template(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    pattern = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
    found = False
    for match in pattern.finditer(template):
        found = True
        parts.append(re.escape(template[cursor:match.start()]))
        var = match.group(1)
        if var not in ALLOWED_TEMPLATE_VARS:
            raise ValueError(f"متغير غير مدعوم: {{{var}}}")
        if var == "amount":
            parts.append(r"(?P<amount>[0-9][0-9,]*)")
        elif var == "user":
            parts.append(r"<@!?(?P<user_id>[0-9]+)>")
        elif var in {"user_id", "recipient_id", "role_id"}:
            parts.append(rf"(?P<{var}>[0-9]+)")
        elif var == "tax":
            parts.append(r"(?P<tax>[0-9]+(?:\.[0-9]+)?)")
        elif var in {"username", "role", "method"}:
            parts.append(rf"(?P<{var}>.+?)")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    if not found:
        parts = [re.escape(template)]
    # Discord payment bots can insert different line breaks/spacing, while the
    # configured literal text remains otherwise exact.
    regex_text = "".join(parts).replace(r"\ ", r"\s+")
    return re.compile(r"^\s*" + regex_text + r"\s*$", re.DOTALL)


def _normal_username_values(member: discord.Member) -> set[str]:
    return {
        str(member.name).strip().casefold(),
        str(member.display_name).strip().casefold(),
        str(member).strip().casefold(),
    }


class PurchaseRoleSelect(discord.ui.Select):
    def __init__(self, cog: "TicketPurchase", user_id: int, rows: list[dict[str, Any]]):
        self.cog = cog
        self.user_id = user_id
        options = []
        for row in rows[:25]:
            role = cog.bot.get_guild(int(row["guild_id"])).get_role(int(row["role_id"])) if cog.bot.get_guild(int(row["guild_id"])) else None
            if role is None:
                continue
            prices = int(row["price_count"])
            options.append(
                discord.SelectOption(
                    label=str(role.name)[:100],
                    description=f"{prices} طريقة دفع متاحة"[:100],
                    value=str(row["role_id"]),
                )
            )
        super().__init__(placeholder="اختر الرتبة التي تريد شراءها", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذه القائمة ليست لك.", ephemeral=True)
        try:
            role_id = int(self.values[0])
        except (ValueError, IndexError):
            return await interaction.response.send_message("❌ الرتبة غير صالحة.", ephemeral=True)
        await self.cog.show_payment_methods(interaction, role_id)


class PurchaseRoleView(discord.ui.View):
    def __init__(self, cog: "TicketPurchase", user_id: int, rows: list[dict[str, Any]]):
        super().__init__(timeout=300)
        self.add_item(PurchaseRoleSelect(cog, user_id, rows))


class CopyTransferButton(discord.ui.Button):
    def __init__(self, transfer_text: str, user_id: int):
        super().__init__(label="نسخ التحويل", emoji="📋", style=discord.ButtonStyle.secondary)
        self.transfer_text = transfer_text
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذا الزر ليس لك.", ephemeral=True)
        await interaction.response.send_message(
            f"📋 انسخ هذا التحويل:\n```{self.transfer_text}```",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class PaymentMethodButton(discord.ui.Button):
    def __init__(self, cog: "TicketPurchase", method: dict[str, Any], role: discord.Role, user_id: int, channel_id: int):
        emoji = _parse_emoji(str(method.get("emoji") or "💳"))
        label = str(method.get("button_label") or method.get("name") or "الدفع")[:80]
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary)
        self.cog = cog
        self.method_id = int(method["id"])
        self.role_id = role.id
        self.user_id = user_id
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ هذه الأزرار ليست لك.", ephemeral=True)
        await self.cog.create_order_and_show_transfer(interaction, self.channel_id, self.role_id, self.method_id)


class PaymentMethodView(discord.ui.View):
    def __init__(self, cog: "TicketPurchase", methods: list[dict[str, Any]], role: discord.Role, user_id: int, channel_id: int):
        super().__init__(timeout=300)
        for method in methods[:MAX_METHOD_BUTTONS]:
            self.add_item(PaymentMethodButton(cog, method, role, user_id, channel_id))


class TicketPurchase(commands.Cog):
    """Configure and process role purchases inside tickets."""

    shop = app_commands.Group(name="ticket-shop", description="إدارة شراء الرتب داخل التذاكر")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ticket_shop_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                button_label TEXT NOT NULL,
                emoji TEXT NOT NULL DEFAULT '💳',
                transfer_bot_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                tax_percent REAL NOT NULL DEFAULT 0,
                transfer_template TEXT NOT NULL DEFAULT '#credits {recipient_id} {amount}',
                success_template TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                UNIQUE(guild_id, name)
            )"""
        )
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ticket_shop_ranks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(guild_id, role_id)
            )"""
        )
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ticket_shop_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                rank_id INTEGER NOT NULL,
                method_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(rank_id, method_id)
            )"""
        )
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS ticket_purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                ticket_channel_id INTEGER NOT NULL,
                buyer_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                method_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                transfer_amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                proof_message_id INTEGER,
                proof_channel_id INTEGER,
                created_at REAL NOT NULL,
                completed_at REAL
            )"""
        )

    async def _ticket(self, guild_id: int, channel_id: int):
        return await self.db.fetchone(
            "SELECT * FROM tickets WHERE guild_id=? AND channel_id=? AND status='open' LIMIT 1",
            (guild_id, channel_id),
        )

    async def _methods_for_role(self, guild_id: int, role_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT m.*, p.price FROM ticket_shop_prices p
               JOIN ticket_shop_methods m ON m.id=p.method_id
               JOIN ticket_shop_ranks r ON r.id=p.rank_id
               WHERE p.guild_id=? AND r.guild_id=? AND r.role_id=? AND m.enabled=1
               ORDER BY m.id ASC""",
            (guild_id, guild_id, role_id),
        )
        return [dict(row) for row in rows]

    async def _role_rows(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT r.guild_id, r.role_id, COUNT(p.id) AS price_count
               FROM ticket_shop_ranks r
               JOIN ticket_shop_prices p ON p.rank_id=r.id
               JOIN ticket_shop_methods m ON m.id=p.method_id AND m.enabled=1
               WHERE r.guild_id=?
               GROUP BY r.guild_id, r.role_id ORDER BY r.id ASC""",
            (guild_id,),
        )
        return [dict(row) for row in rows]

    async def _admin(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        return bool(
            interaction.guild
            and isinstance(member, discord.Member)
            and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)
        )

    @staticmethod
    def _gross_amount(price: int, tax_percent: float) -> int:
        if tax_percent <= 0:
            return int(price)
        # The configured rank price is the amount the recipient should net.
        # Round up so the recipient is never short because of payment tax.
        return max(int(price), int((price / (1 - tax_percent / 100)) + 0.999999))

    async def open_purchase(self, interaction: discord.Interaction, channel_id: int):
        if not interaction.guild:
            return await interaction.response.send_message("❌ الشراء خدام غير داخل السيرفر.", ephemeral=True)
        ticket = await self._ticket(interaction.guild.id, channel_id)
        if not ticket:
            return await interaction.response.send_message("❌ هادي ماشي تذكرة مفتوحة.", ephemeral=True)
        if int(ticket["user_id"]) != interaction.user.id:
            return await interaction.response.send_message("❌ الشراء داخل التذكرة مخصص لصاحب التذكرة.", ephemeral=True)
        rows = await self._role_rows(interaction.guild.id)
        rows = [row for row in rows if interaction.guild.get_role(int(row["role_id"])) is not None]
        if not rows:
            return await interaction.response.send_message("❌ ما كايناش حتى رتبة متاحة للشراء حالياً.", ephemeral=True)
        embed = discord.Embed(
            title="🛒 شراء رتبة",
            description="**اختر الرتبة التي تريد شراءها**",
            colour=discord.Colour.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=PurchaseRoleView(self, interaction.user.id, rows), ephemeral=True)

    async def show_payment_methods(self, interaction: discord.Interaction, role_id: int):
        if not interaction.guild:
            return await interaction.response.send_message("❌ السيرفر غير موجود.", ephemeral=True)
        ticket = await self._ticket(interaction.guild.id, interaction.channel.id)
        if not ticket or int(ticket["user_id"]) != interaction.user.id:
            return await interaction.response.send_message("❌ ما عندكش صلاحية تكمل هاد الشراء.", ephemeral=True)
        role = interaction.guild.get_role(role_id)
        if role is None:
            return await interaction.response.send_message("❌ الرتبة ما بقاتش موجودة.", ephemeral=True)
        if role.is_default() or role.managed:
            return await interaction.response.send_message("❌ ما يمكنش شراء هاد الرتبة.", ephemeral=True)
        methods = await self._methods_for_role(interaction.guild.id, role_id)
        if not methods:
            return await interaction.response.edit_message(content="❌ ما كايناش طريقة دفع متاحة لهاد الرتبة.", embed=None, view=None)
        price_lines = []
        for method in methods[:10]:
            gross = self._gross_amount(int(method["price"]), float(method["tax_percent"] or 0))
            price_lines.append(f"{method['emoji']} **{method['button_label']}** — `{gross:,}`")
        embed = discord.Embed(
            title=f"💳 شراء {role.name}",
            description="**اختر طريقة الدفع**\n\n" + "\n".join(price_lines),
            colour=discord.Colour.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=PaymentMethodView(self, methods, role, interaction.user.id, interaction.channel.id))

    async def create_order_and_show_transfer(self, interaction: discord.Interaction, channel_id: int, role_id: int, method_id: int):
        if not interaction.guild:
            return await interaction.response.send_message("❌ السيرفر غير موجود.", ephemeral=True)
        ticket = await self._ticket(interaction.guild.id, channel_id)
        if not ticket or int(ticket["user_id"]) != interaction.user.id:
            return await interaction.response.send_message("❌ هاد الشراء ما بقاش صالح.", ephemeral=True)
        role = interaction.guild.get_role(role_id)
        method_row = await self.db.fetchone(
            "SELECT m.*, p.price FROM ticket_shop_methods m JOIN ticket_shop_prices p ON p.method_id=m.id WHERE m.guild_id=? AND m.id=? AND m.enabled=1 AND p.rank_id=(SELECT id FROM ticket_shop_ranks WHERE guild_id=? AND role_id=? LIMIT 1)",
            (interaction.guild.id, method_id, interaction.guild.id, role_id),
        )
        if role is None or role.is_default() or role.managed or not method_row:
            return await interaction.response.send_message("❌ الرتبة أو طريقة الدفع غير صالحة.", ephemeral=True)
        existing = await self.db.fetchone(
            "SELECT * FROM ticket_purchase_orders WHERE guild_id=? AND ticket_channel_id=? AND buyer_id=? AND status='pending' LIMIT 1",
            (interaction.guild.id, channel_id, interaction.user.id),
        )
        if existing:
            return await interaction.response.send_message(
                f"⏳ عندك عملية شراء معلقة بالفعل. حول `{int(existing['transfer_amount']):,}` ثم تسنى التحقق.",
                ephemeral=True,
            )
        price = int(method_row["price"])
        tax = float(method_row["tax_percent"] or 0)
        transfer_amount = self._gross_amount(price, tax)
        transfer_text = _format_transfer(
            str(method_row["transfer_template"]),
            recipient_id=int(method_row["recipient_id"]),
            amount=transfer_amount,
            price=price,
            tax=tax,
            user_id=interaction.user.id,
            role=role.name,
            role_id=role.id,
            method=str(method_row["name"]),
            username=interaction.user.name,
        )
        await self.db.execute(
            "INSERT INTO ticket_purchase_orders(guild_id,ticket_channel_id,buyer_id,role_id,method_id,price,transfer_amount,status,created_at) VALUES(?,?,?,?,?,?,?,'pending',?)",
            (interaction.guild.id, channel_id, interaction.user.id, role.id, method_id, price, transfer_amount, time.time()),
        )
        embed = discord.Embed(
            title="💳 معلومات التحويل",
            description=(
                f"الرتبة: **{role.name}**\n"
                f"السعر: **{price:,} ANORIS**\n"
                f"المبلغ المطلوب للتحويل: **{transfer_amount:,}**\n\n"
                f"```{transfer_text}```\n\n"
                "بعد ما يدوز التحويل، البوت غادي يتحقق من رسالة التحويل تلقائياً ويعطيك الرتبة."
            ),
            colour=discord.Colour.blurple(),
        )
        view = discord.ui.View(timeout=300)
        view.add_item(CopyTransferButton(transfer_text, interaction.user.id))
        await interaction.response.edit_message(embed=embed, view=view)

    async def _complete_order(self, message: discord.Message, order: dict[str, Any], method: dict[str, Any]) -> bool:
        guild = message.guild
        if guild is None:
            return False
        role = guild.get_role(int(order["role_id"]))
        buyer = guild.get_member(int(order["buyer_id"]))
        if buyer is None:
            try:
                buyer = await guild.fetch_member(int(order["buyer_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False
        if role is None or role.is_default() or role.managed:
            return False
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles or me.top_role <= role:
            channel = guild.get_channel(int(order["ticket_channel_id"]))
            if channel:
                await channel.send("❌ تم التحقق من التحويل، ولكن البوت لا يستطيع إعطاء الرتبة بسبب ترتيب الرتب أو Manage Roles.")
            return False
        try:
            await buyer.add_roles(role, reason=f"Ader ticket purchase #{order['id']}")
        except (discord.Forbidden, discord.HTTPException):
            return False
        await self.db.execute(
            "UPDATE ticket_purchase_orders SET status='completed', proof_message_id=?, proof_channel_id=?, completed_at=? WHERE id=? AND status='pending'",
            (message.id, message.channel.id, time.time(), int(order["id"])),
        )
        ticket_channel = guild.get_channel(int(order["ticket_channel_id"]))
        if ticket_channel:
            await ticket_channel.send(
                f"✅ **تم تأكيد التحويل بنجاح!**\nتم منح الرتبة {role.mention} للعضو **{buyer.name}**.",
                allowed_mentions=discord.AllowedMentions(roles=True, users=False),
            )
        return True

    async def on_message(self, message: discord.Message):
        if message.guild is None or not message.author.bot or not message.content:
            return
        rows = await self.db.fetchall(
            """SELECT o.*, m.name AS method_name, m.transfer_bot_id, m.recipient_id, m.tax_percent, m.success_template
               FROM ticket_purchase_orders o JOIN ticket_shop_methods m ON m.id=o.method_id
               WHERE o.guild_id=? AND o.status='pending' AND o.created_at>=?""",
            (message.guild.id, time.time() - ORDER_TIMEOUT),
        )
        if not rows:
            return
        text = message.content
        for emb in message.embeds:
            if emb.title:
                text += "\n" + emb.title
            if emb.description:
                text += "\n" + emb.description
            for field in emb.fields:
                text += f"\n{field.name}\n{field.value}"
        for row in rows:
            if int(row["transfer_bot_id"]) != int(message.author.id):
                continue
            method = dict(row)
            try:
                match = _compile_success_template(str(row["success_template"])).match(text)
            except ValueError:
                continue
            if not match:
                continue
            groups = match.groupdict()
            try:
                detected_amount = int(str(groups.get("amount", "0")).replace(",", "")) if groups.get("amount") else None
            except ValueError:
                continue
            if detected_amount != int(row["transfer_amount"]):
                continue
            if groups.get("recipient_id") and int(groups["recipient_id"]) != int(row["recipient_id"]):
                continue
            if groups.get("user_id") and int(groups["user_id"]) != int(row["buyer_id"]):
                continue
            if groups.get("user_id") is not None and groups.get("user_id") != str(row["buyer_id"]):
                continue
            if "user" in str(row["success_template"]):
                parsed_user = groups.get("user_id")
                if parsed_user and int(parsed_user) != int(row["recipient_id"]):
                    continue
            if groups.get("username"):
                buyer = message.guild.get_member(int(row["buyer_id"]))
                if buyer and groups["username"].strip().casefold() not in _normal_username_values(buyer):
                    continue
            await self._complete_order(message, dict(row), method)

    @shop.command(name="method-add", description="إضافة طريقة دفع لشراء الرتب داخل التذاكر")
    @app_commands.describe(
        name="اسم داخلي لطريقة الدفع",
        button_label="اسم الزر الذي سيظهر للعميل",
        emoji="إيموجي عادي أو Custom Emoji مثل <:name:id>",
        transfer_bot_id="ID ديال بوت التحويل الموثوق",
        recipient_id="ID ديال الحساب الذي سيستقبل التحويل",
        tax_percent="الضريبة بالمئة، مثال 5",
        transfer_template="أمر التحويل؛ استعمل {recipient_id} و {amount}",
        success_template="رسالة نجاح التحويل بالضبط؛ استعمل {username} و {amount} و {user} وغيرها",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def method_add(self, interaction: discord.Interaction, name: str, button_label: str, emoji: str, transfer_bot_id: str, recipient_id: str, tax_percent: float, transfer_template: str, success_template: str):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        try:
            bot_id = int(transfer_bot_id)
            target_id = int(recipient_id)
        except ValueError:
            return await interaction.response.send_message("❌ IDs خاصهم يكونو أرقام فقط.", ephemeral=True)
        if not 0 <= tax_percent < 100:
            return await interaction.response.send_message("❌ الضريبة خاصها تكون بين 0 و 99.99%.", ephemeral=True)
        try:
            _ = _format_transfer(transfer_template, recipient_id=target_id, amount=100, price=100, tax=tax_percent, user_id=interaction.user.id, role="Role", role_id=1, method=name, username=interaction.user.name)
            _ = _compile_success_template(success_template)
        except (KeyError, ValueError) as exc:
            return await interaction.response.send_message(f"❌ Template غير صالح: {exc}", ephemeral=True)
        try:
            await self.db.execute(
                "INSERT INTO ticket_shop_methods(guild_id,name,button_label,emoji,transfer_bot_id,recipient_id,tax_percent,transfer_template,success_template,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,1,?)",
                (interaction.guild.id, name[:80], button_label[:80], emoji[:50] or "💳", bot_id, target_id, float(tax_percent), transfer_template[:1500], success_template[:2000], time.time()),
            )
        except Exception:
            return await interaction.response.send_message("❌ كاينة طريقة دفع بنفس الاسم مسبقاً.", ephemeral=True)
        await interaction.response.send_message(f"✅ تمت إضافة طريقة الدفع **{button_label}** بنجاح.", ephemeral=True)

    @shop.command(name="method-remove", description="حذف طريقة دفع")
    @app_commands.describe(method_id="ID ديال طريقة الدفع")
    @app_commands.default_permissions(manage_guild=True)
    async def method_remove(self, interaction: discord.Interaction, method_id: int):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        row = await self.db.fetchone("SELECT id FROM ticket_shop_methods WHERE id=? AND guild_id=?", (method_id, interaction.guild.id))
        if not row:
            return await interaction.response.send_message("❌ طريقة الدفع غير موجودة.", ephemeral=True)
        await self.db.execute("DELETE FROM ticket_shop_prices WHERE method_id=?", (method_id,))
        await self.db.execute("DELETE FROM ticket_shop_methods WHERE id=? AND guild_id=?", (method_id, interaction.guild.id))
        await interaction.response.send_message("✅ تحيدات طريقة الدفع والأسعار المرتبطة بها.", ephemeral=True)

    @shop.command(name="method-list", description="عرض طرق الدفع")
    @app_commands.default_permissions(manage_guild=True)
    async def method_list(self, interaction: discord.Interaction):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        rows = await self.db.fetchall("SELECT * FROM ticket_shop_methods WHERE guild_id=? ORDER BY id", (interaction.guild.id,))
        if not rows:
            return await interaction.response.send_message("ℹ️ ما كايناش حتى طريقة دفع.", ephemeral=True)
        lines = [f"**#{r['id']}** {r['emoji']} `{r['button_label']}` — Bot: `{r['transfer_bot_id']}` — Recipient: `{r['recipient_id']}` — Tax: `{r['tax_percent']}%`" for r in rows]
        await interaction.response.send_message("\n".join(lines[:25]), ephemeral=True)

    @shop.command(name="rank-add", description="إضافة رتبة للمتجر داخل التذاكر")
    @app_commands.describe(role="الرتبة التي سيتم بيعها")
    @app_commands.default_permissions(manage_guild=True)
    async def rank_add(self, interaction: discord.Interaction, role: discord.Role):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        if role.is_default() or role.managed:
            return await interaction.response.send_message("❌ هاد الرتبة ما صالحةش للبيع.", ephemeral=True)
        if interaction.guild.me and role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("❌ خاص رتبة البوت تكون أعلى من الرتبة التي غادي يتباع بها.", ephemeral=True)
        try:
            await self.db.execute("INSERT INTO ticket_shop_ranks(guild_id,role_id,created_at) VALUES(?,?,?)", (interaction.guild.id, role.id, time.time()))
        except Exception:
            return await interaction.response.send_message("❌ هاد الرتبة مضافة من قبل.", ephemeral=True)
        await interaction.response.send_message(f"✅ تمت إضافة {role.mention}. دابا خصك تحدد ثمنها عبر `ticket-shop price-set`.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @shop.command(name="rank-remove", description="حذف رتبة من متجر التذاكر")
    @app_commands.describe(role="الرتبة")
    @app_commands.default_permissions(manage_guild=True)
    async def rank_remove(self, interaction: discord.Interaction, role: discord.Role):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        row = await self.db.fetchone("SELECT id FROM ticket_shop_ranks WHERE guild_id=? AND role_id=?", (interaction.guild.id, role.id))
        if not row:
            return await interaction.response.send_message("❌ الرتبة ماشي مضافة.", ephemeral=True)
        await self.db.execute("DELETE FROM ticket_shop_prices WHERE rank_id=?", (row["id"],))
        await self.db.execute("DELETE FROM ticket_shop_ranks WHERE id=?", (row["id"],))
        await interaction.response.send_message("✅ تحيدات الرتبة وأسعارها.", ephemeral=True)

    @shop.command(name="price-set", description="تحديد سعر رتبة حسب طريقة دفع")
    @app_commands.describe(role="الرتبة", method_id="ID طريقة الدفع", price="السعر الصافي الذي يجب أن يصلك")
    @app_commands.default_permissions(manage_guild=True)
    async def price_set(self, interaction: discord.Interaction, role: discord.Role, method_id: int, price: int):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        if price <= 0:
            return await interaction.response.send_message("❌ السعر خاصو يكون أكبر من 0.", ephemeral=True)
        rank = await self.db.fetchone("SELECT id FROM ticket_shop_ranks WHERE guild_id=? AND role_id=?", (interaction.guild.id, role.id))
        method = await self.db.fetchone("SELECT id,button_label FROM ticket_shop_methods WHERE guild_id=? AND id=?", (interaction.guild.id, method_id))
        if not rank or not method:
            return await interaction.response.send_message("❌ الرتبة أو طريقة الدفع غير موجودة.", ephemeral=True)
        await self.db.execute(
            "INSERT INTO ticket_shop_prices(guild_id,rank_id,method_id,price,created_at) VALUES(?,?,?,?,?) ON CONFLICT(rank_id,method_id) DO UPDATE SET price=excluded.price",
            (interaction.guild.id, rank["id"], method_id, price, time.time()),
        )
        await interaction.response.send_message(f"✅ ثمن {role.mention} عبر **{method['button_label']}** أصبح **{price:,}** صافي المبلغ.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @shop.command(name="price-remove", description="حذف سعر رتبة لطريقة دفع")
    @app_commands.describe(role="الرتبة", method_id="ID طريقة الدفع")
    @app_commands.default_permissions(manage_guild=True)
    async def price_remove(self, interaction: discord.Interaction, role: discord.Role, method_id: int):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        rank = await self.db.fetchone("SELECT id FROM ticket_shop_ranks WHERE guild_id=? AND role_id=?", (interaction.guild.id, role.id))
        if not rank:
            return await interaction.response.send_message("❌ الرتبة ماشي مضافة.", ephemeral=True)
        cur = await self.db.execute("DELETE FROM ticket_shop_prices WHERE rank_id=? AND method_id=?", (rank["id"], method_id))
        await interaction.response.send_message("✅ تحيد السعر." if cur.rowcount else "❌ السعر غير موجود.", ephemeral=True)

    @shop.command(name="list", description="عرض الرتب والأسعار وطرق الدفع")
    @app_commands.default_permissions(manage_guild=True)
    async def list_shop(self, interaction: discord.Interaction):
        if not await self._admin(interaction):
            return await interaction.response.send_message("❌ تحتاج Manage Server أو Administrator.", ephemeral=True)
        rows = await self.db.fetchall(
            """SELECT r.role_id, m.button_label, m.emoji, m.tax_percent, p.price, m.id AS method_id
               FROM ticket_shop_ranks r JOIN ticket_shop_prices p ON p.rank_id=r.id
               JOIN ticket_shop_methods m ON m.id=p.method_id
               WHERE r.guild_id=? ORDER BY r.role_id, m.id""",
            (interaction.guild.id,),
        )
        if not rows:
            return await interaction.response.send_message("ℹ️ متجر التذاكر فارغ حالياً.", ephemeral=True)
        lines = []
        for row in rows[:40]:
            role = interaction.guild.get_role(int(row["role_id"]))
            role_name = role.name if role else f"Role {row['role_id']}"
            gross = self._gross_amount(int(row["price"]), float(row["tax_percent"] or 0))
            lines.append(f"{role_name} → {row['emoji']} **{row['button_label']}** | صافي `{int(row['price']):,}` | تحويل `{gross:,}` | tax `{row['tax_percent']}%`")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketPurchase(bot))
