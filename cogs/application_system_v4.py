from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

SET2 = "📝"
GGG = "📋"

DEFAULT_QUESTIONS = [
    {"label": "ما الاسم الذي تفضّل استخدامه؟", "required": True, "paragraph": False},
    {"label": "ما خبرتك السابقة في إدارة السيرفرات؟", "required": True, "paragraph": True},
    {"label": "ما الوقت الذي يمكنك تخصيصه أسبوعياً؟", "required": True, "paragraph": False},
    {"label": "كيف ستتعامل مع خلاف بين عضوين؟", "required": True, "paragraph": True},
    {"label": "لماذا ترغب في الانضمام إلى فريق الإدارة؟", "required": True, "paragraph": True},
    {"label": "ما الإضافة التي يمكنك تقديمها للفريق؟", "required": False, "paragraph": True},
]

STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def panel_default(number: int) -> dict:
    return {
        "title": f"التقديم للانضمام إلى الإدارة",
        "description": "نبحث عن أعضاء مسؤولين ومتعاونين للمساهمة في تنظيم المجتمع ومساعدة أعضائه.",
        "questions": [dict(q) for q in DEFAULT_QUESTIONS],
        "button_label": "ابدأ التقديم",
        "button_emoji": "📝",
        "button_style": "primary",
        "image": None,
        "results": None,
        "accept_role": None,
        "reject_role": None,
        "published_channel_id": None,
        "published_message_id": None,
    }


class AppV4(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.answers: dict[tuple[int, int, int], list[str]] = {}

    async def cog_load(self):
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                panel INTEGER NOT NULL,
                status TEXT NOT NULL,
                answers TEXT NOT NULL,
                created_at REAL NOT NULL,
                reviewer_id INTEGER,
                reason TEXT
            )"""
        )
        await self.bot.db.execute(
            """CREATE TABLE IF NOT EXISTS application_panels (
                guild_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (guild_id, number)
            )"""
        )
        columns = {row["name"] for row in await self.bot.db.fetchall("PRAGMA table_info(applications)")}
        for name, definition in (
            ("results_channel_id", "INTEGER"),
            ("results_message_id", "INTEGER"),
        ):
            if name not in columns:
                await self.bot.db.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")

        # Migrate saved panels from the legacy settings table without replacing
        # the unrelated guild settings stored there.
        legacy = await self.bot.db.fetchall(
            "SELECT guild_id, key, value FROM settings WHERE key LIKE 'app_panel_%'"
        )
        for row in legacy:
            try:
                number = int(str(row["key"]).rsplit("_", 1)[-1])
            except (TypeError, ValueError):
                continue
            if 1 <= number <= 3:
                await self.bot.db.execute(
                    "INSERT OR IGNORE INTO application_panels(guild_id, number, value) VALUES(?,?,?)",
                    (int(row["guild_id"]), number, str(row["value"] or "{}")),
                )

        panels = await self.bot.db.fetchall(
            "SELECT guild_id, number, value FROM application_panels"
        )
        for row in panels:
            try:
                panel = panel_default(int(row["number"]))
                panel.update(json.loads(row["value"] or "{}"))
                message_id = int(panel.get("published_message_id") or 0)
                guild_id = int(row["guild_id"])
                number = int(row["number"])
                if message_id:
                    self.bot.add_view(
                        PublishedApplication(self, guild_id, number, panel),
                        message_id=message_id,
                    )
            except Exception:
                continue

        pending = await self.bot.db.fetchall(
            "SELECT id, results_message_id FROM applications "
            "WHERE status='pending' AND results_message_id IS NOT NULL"
        )
        for row in pending:
            try:
                self.bot.add_view(ReviewView(self, int(row["id"])), message_id=int(row["results_message_id"]))
            except Exception:
                continue

    async def get_panel(self, guild_id: int, number: int) -> dict:
        row = await self.bot.db.fetchone(
            "SELECT value FROM application_panels WHERE guild_id=? AND number=?",
            (guild_id, number),
        )
        if row is None:
            row = await self.bot.db.fetchone(
                "SELECT value FROM settings WHERE guild_id=? AND key=?",
                (guild_id, f"app_panel_{number}"),
            )
        panel = panel_default(number)
        if row:
            try:
                saved = json.loads(row["value"])
                if isinstance(saved, dict):
                    panel.update(saved)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        if not isinstance(panel.get("questions"), list) or not panel["questions"]:
            panel["questions"] = [dict(q) for q in DEFAULT_QUESTIONS]
        panel["questions"] = panel["questions"][:25]
        return panel

    async def save_panel(self, guild_id: int, number: int, panel: dict):
        await self.bot.db.execute(
            """INSERT INTO application_panels(guild_id, number, value) VALUES(?,?,?)
               ON CONFLICT(guild_id, number) DO UPDATE SET value=excluded.value""",
            (guild_id, number, json.dumps(panel, ensure_ascii=False)),
        )

    async def publish_panel(self, guild: discord.Guild, number: int, channel: discord.TextChannel):
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("اختر قناة نصية صالحة لنشر لوحة التقديم.")
        panel = await self.get_panel(guild.id, number)
        if not panel.get("results"):
            raise ValueError("حدد قناة استقبال طلبات التقديم أولاً من إعدادات اللوحة.")
        color_value = str(panel.get("color") or "#7657F6").strip().lstrip("#")
        try:
            color = int(color_value, 16)
        except ValueError:
            color = 0x7657F6
        embed = discord.Embed(
            title=str(panel.get("title") or "التقديم للانضمام إلى الإدارة")[:256],
            description=str(panel.get("description") or "اضغط على الزر أدناه لبدء التقديم.")[:4000],
            color=color,
        )
        embed.add_field(name="📋 خطوات التقديم", value="1. اضغط على زر التقديم.\n2. أجب عن الأسئلة بوضوح.\n3. سيقوم فريق الإدارة بمراجعة طلبك.", inline=False)
        embed.set_footer(text="ANO BOT • نظام تقديم الإدارة")
        image = str(panel.get("image") or "").strip()
        if image.startswith("https://"):
            embed.set_image(url=image)

        view = PublishedApplication(self, guild.id, number, panel)
        message = None
        old_channel_id = int(panel.get("published_channel_id") or 0)
        old_message_id = int(panel.get("published_message_id") or 0)
        if old_channel_id == channel.id and old_message_id:
            try:
                old_message = await channel.fetch_message(old_message_id)
                await old_message.edit(embed=embed, view=view)
                message = old_message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is None:
            message = await channel.send(embed=embed, view=view)

        panel["published_channel_id"] = channel.id
        panel["published_message_id"] = message.id
        await self.save_panel(guild.id, number, panel)
        return message

    async def send_error(self, interaction: discord.Interaction, text: str):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="تقديم", description="إعداد تقديمات الإدارة")
    @app_commands.describe(اخفاء="إخفاء لوحة إعداد التقديم")
    @app_commands.checks.has_permissions(administrator=True)
    async def تقديم(self, interaction: discord.Interaction, اخفاء: bool = False):
        await interaction.response.send_message(
            embed=discord.Embed(description=f"** اختر التقديم الذي تود التعديل عليه {GGG}**"),
            view=PanelPicker(self),
            ephemeral=اخفاء,
        )

    async def open_settings(self, interaction: discord.Interaction, number: int):
        await interaction.response.edit_message(
            embed=discord.Embed(description=f"** اعدادات تقديم {number} {SET2} من هنا **"),
            view=PanelSettings(self, number),
        )

    async def edit_panel(self, interaction: discord.Interaction, number: int, action: str):
        panel = await self.get_panel(interaction.guild.id, number)
        if action == "title":
            await interaction.response.send_modal(TextEditModal(self, number, "title", "تعديل عنوان Panel", "عنوان Panel", panel["title"]))
        elif action == "button_name":
            await interaction.response.send_modal(TextEditModal(self, number, "button_label", "اسم زر التقديم", "اسم الزر", panel["button_label"]))
        elif action == "emoji":
            await interaction.response.send_modal(TextEditModal(self, number, "button_emoji", "إيموجي التقديم", "Unicode أو <:name:id>", panel.get("button_emoji") or ""))
        elif action == "button":
            await interaction.response.send_message(
                embed=discord.Embed(description=f"** إعدادات زر تقديم {number} {SET2} **"),
                view=ButtonSettings(self, number), ephemeral=True,
            )
        elif action == "questions":
            await interaction.response.send_message(
                embed=discord.Embed(description=f"** تعديل أسئلة تقديم {number} {SET2} **"),
                view=QuestionSettings(self, number), ephemeral=True,
            )
        elif action == "results":
            await interaction.response.send_message(
                "**اختر الروم الذي ستصل إليه سجلات ونتائج التقديم**",
                view=ChannelPicker(self, number), ephemeral=True,
            )
        elif action == "roles":
            await interaction.response.send_message(
                embed=discord.Embed(description=f"** إعدادات رتب تقديم {number} {SET2} **"),
                view=RoleSettings(self, number), ephemeral=True,
            )
        elif action == "image":
            await interaction.response.send_message("أرسل صورة التقديم هنا خلال **5 دقائق**.", ephemeral=True)
            try:
                message = await self.bot.wait_for(
                    "message", timeout=300,
                    check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id and bool(m.attachments),
                )
                attachment = message.attachments[0]
                if not (attachment.content_type or "").startswith("image/"):
                    return await interaction.followup.send("❌ الملف المرسل ليس صورة.", ephemeral=True)
                panel["image"] = attachment.url
                await self.save_panel(interaction.guild.id, number, panel)
                await interaction.followup.send("**تم تحديد صورة لي التقديم**", ephemeral=True)
            except asyncio.TimeoutError:
                await interaction.followup.send("⌛ انتهت مهلة 5 دقائق.", ephemeral=True)
        elif action == "back":
            await interaction.response.edit_message(
                embed=discord.Embed(description=f"** اختر التقديم الذي تود التعديل عليه {GGG}**"),
                view=PanelPicker(self),
            )
        elif action == "send":
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                return await self.send_error(interaction, "❌ خاص إرسال التقديم من روم نصي.")
            try:
                message = await self.publish_panel(interaction.guild, number, channel)
            except (ValueError, discord.Forbidden, discord.HTTPException) as exc:
                return await self.send_error(interaction, f"تعذر نشر لوحة التقديم: {str(exc)[:250]}")
            await interaction.response.send_message(f"✅ تم نشر لوحة التقديم في {message.channel.mention}.", ephemeral=True)

    async def save_question(self, interaction: discord.Interaction, number: int, index: int, label: str, required: bool, paragraph: bool):
        panel = await self.get_panel(interaction.guild.id, number)
        if not 0 <= index < 25:
            return await self.send_error(interaction, "الحد الأقصى للأسئلة هو 25 سؤالاً.")
        clean_label = label.strip()[:45]
        if not clean_label:
            return await self.send_error(interaction, "اكتب نص السؤال قبل الحفظ.")
        while len(panel["questions"]) <= index:
            panel["questions"].append({"label": f"سؤال {len(panel['questions']) + 1}", "required": True, "paragraph": False})
        panel["questions"][index] = {"label": clean_label, "required": bool(required), "paragraph": bool(paragraph)}
        panel["questions"] = panel["questions"][:25]
        await self.save_panel(interaction.guild.id, number, panel)
        await interaction.response.send_message("✅ تم حفظ السؤال.", ephemeral=True)

    async def select_results(self, interaction: discord.Interaction, number: int, channel_id: int):
        panel = await self.get_panel(interaction.guild.id, number)
        panel["results"] = channel_id
        await self.save_panel(interaction.guild.id, number, panel)
        await interaction.response.send_message("✅ تم تحديد روم سجلات التقديمات بنجاح.", ephemeral=True)

    async def set_role(self, interaction: discord.Interaction, number: int, key: str, role_id: Optional[int]):
        panel = await self.get_panel(interaction.guild.id, number)
        panel[key] = role_id
        await self.save_panel(interaction.guild.id, number, panel)
        await interaction.response.send_message("✅ تم حفظ إعداد الرتبة.", ephemeral=True)

    async def start_application(self, interaction: discord.Interaction, number: int):
        pending = await self.bot.db.fetchone(
            "SELECT id FROM applications WHERE guild_id=? AND user_id=? AND panel=? AND status='pending' LIMIT 1",
            (interaction.guild.id, interaction.user.id, number),
        )
        if pending:
            return await interaction.response.send_message(
                "📨 لديك طلب تقديم قيد المراجعة بالفعل. انتظر قرار الإدارة قبل إرسال طلب آخر.",
                ephemeral=True,
            )
        panel = await self.get_panel(interaction.guild.id, number)
        questions = panel["questions"]
        self.answers[(interaction.guild.id, interaction.user.id, number)] = []
        await interaction.response.send_modal(ApplicationModal(self, number, 0, questions))

    async def collect(self, interaction: discord.Interaction, number: int, questions: list[dict]):
        key = (interaction.guild.id, interaction.user.id, number)
        values = self.answers.get(key, []) + [str(field.value) for field in interaction.fields]
        self.answers[key] = values
        if len(values) < len(questions):
            return await interaction.response.send_modal(ApplicationModal(self, number, len(values), questions))
        self.answers.pop(key, None)
        pending = await self.bot.db.fetchone(
            "SELECT id FROM applications WHERE guild_id=? AND user_id=? AND panel=? AND status='pending' LIMIT 1",
            (interaction.guild.id, interaction.user.id, number),
        )
        if pending:
            return await interaction.response.send_message(
                "📨 لديك طلب تقديم قيد المراجعة بالفعل.",
                ephemeral=True,
            )
        panel = await self.get_panel(interaction.guild.id, number)
        cursor = await self.bot.db.execute(
            "INSERT INTO applications(guild_id,user_id,panel,status,answers,created_at) VALUES(?,?,?,?,?,?)",
            (interaction.guild.id, interaction.user.id, number, "pending", json.dumps(values, ensure_ascii=False), time.time()),
        )
        application_id = cursor.lastrowid
        results = interaction.guild.get_channel(panel.get("results") or 0)
        if results and isinstance(results, discord.TextChannel):
            embed = discord.Embed(
                title=f"📨 طلب تقديم جديد • اللوحة {number}",
                description=f"👤 **المتقدم:** {interaction.user.mention}\n⏳ **الحالة:** قيد المراجعة\n🆔 **رقم الطلب:** #{application_id}",
                color=0x8B5CF6,
                timestamp=discord.utils.utcnow(),
            )
            for index, (question, answer) in enumerate(zip(questions, values), start=1):
                embed.add_field(
                    name=f"❔ {index}. {question['label']}"[:256],
                    value=(answer[:1024] or "—"),
                    inline=False,
                )
            embed.set_footer(text="ANO BOT • مراجعة طلبات الإدارة")
            result_message = await results.send(embed=embed, view=ReviewView(self, application_id))
            await self.bot.db.execute(
                "UPDATE applications SET results_channel_id=?, results_message_id=? WHERE id=?",
                (results.id, result_message.id, application_id),
            )
        await self.bot.db.record_analytics(
            interaction.guild.id,
            "application.submitted",
            {"application_id": application_id, "user_id": interaction.user.id, "panel": number},
        )
        await interaction.response.send_message("✅ تم إرسال التقديم بنجاح. سيصلك القرار عبر رسالة خاصة إن كانت متاحة.", ephemeral=True)

    async def review_application(
        self,
        guild: discord.Guild,
        application_id: int,
        reviewer_id: int,
        accepted: bool,
        reason: Optional[str] = None,
        source_message: Optional[discord.Message] = None,
    ) -> bool:
        row = await self.bot.db.fetchone(
            "SELECT * FROM applications WHERE id=? AND guild_id=?",
            (application_id, guild.id),
        )
        if not row or row["status"] != "pending":
            return False

        panel = await self.get_panel(guild.id, int(row["panel"]))
        status = "accepted" if accepted else "rejected"
        await self.bot.db.execute(
            "UPDATE applications SET status=?, reviewer_id=?, reason=? WHERE id=? AND guild_id=? AND status='pending'",
            (status, reviewer_id, reason, application_id, guild.id),
        )

        role_id = panel.get("accept_role") if accepted else panel.get("reject_role")
        member = guild.get_member(int(row["user_id"]))
        if member is None and role_id:
            try:
                member = await guild.fetch_member(int(row["user_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member and role_id:
            role = guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role, reason="مراجعة تقديم الإدارة")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        message = source_message
        if message is None:
            channel_id = int(row["results_channel_id"] or 0)
            message_id = int(row["results_message_id"] or 0)
            channel = guild.get_channel(channel_id) if channel_id else None
            if isinstance(channel, discord.TextChannel) and message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
        if message:
            try:
                embed = message.embeds[0] if message.embeds else discord.Embed(title=f"تقديم {row['panel']}")
                state = "✅ مقبول" if accepted else "❌ مرفوض"
                embed.description = (
                    f"👤 **المتقدم:** <@{row['user_id']}>\n"
                    f"📌 **الحالة:** {state}\n"
                    f"🛡️ **المراجع:** <@{reviewer_id}>"
                    + (f"\n📝 **السبب:** {reason}" if reason else "")
                )
                embed.color = discord.Color.green() if accepted else discord.Color.red()
                await message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

        try:
            await self.bot.db.record_analytics(
                guild.id,
                "application.reviewed",
                {
                    "application_id": application_id,
                    "user_id": int(row["user_id"]),
                    "reviewer_id": reviewer_id,
                    "status": status,
                    "reason": reason or "",
                },
            )
        except Exception:
            pass

        if member:
            try:
                await member.send(
                    embed=discord.Embed(
                        title="تحديث طلب التقديم",
                        description=f"{'✅ تم قبول' if accepted else '❌ تم رفض'} طلبك للانضمام إلى فريق الإدارة في **{guild.name}**."
                        + (f"\n**السبب:** {reason}" if reason else ""),
                        color=discord.Color.green() if accepted else discord.Color.red(),
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        return True

    async def review_from_dashboard(
        self,
        guild: discord.Guild,
        application_id: int,
        reviewer_id: int,
        accepted: bool,
        reason: Optional[str] = None,
    ) -> bool:
        return await self.review_application(guild, application_id, reviewer_id, accepted, reason)

    async def review(self, interaction: discord.Interaction, application_id: int, accepted: bool, reason: Optional[str] = None):
        if not await self.review_application(
            interaction.guild,
            application_id,
            interaction.user.id,
            accepted,
            reason,
            source_message=interaction.message,
        ):
            return await self.send_error(interaction, "❌ تمت مراجعة هذا التقديم مسبقاً أو أنه غير موجود في هذا السيرفر.")
        await interaction.response.send_message("✅ تمت مراجعة التقديم بنجاح.", ephemeral=True)


class PanelPicker(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=600)
        for number in range(1, 4):
            self.add_item(PanelButton(cog, number))


class PanelButton(discord.ui.Button):
    def __init__(self, cog, number):
        super().__init__(label=f"تقديم {number}", style=discord.ButtonStyle.primary, emoji=SET2)
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await self.cog.open_settings(interaction, self.number)


class PanelSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=600)
        actions = [("تعديل عنوان Panel", "title"), ("تعديل الأسئلة", "questions"), ("تعديل الزر", "button"), ("تحديد صورة لي التقديم", "image"), ("تحديد مكان نتائج التقديم", "results"), ("إعدادات رتب التقديم", "roles"), ("رجوع", "back"), ("إرسال", "send")]
        for label, action in actions:
            self.add_item(PanelAction(cog, number, label, action, discord.ButtonStyle.success if action == "send" else discord.ButtonStyle.secondary if action == "back" else discord.ButtonStyle.primary))


class PanelAction(discord.ui.Button):
    def __init__(self, cog, number, label, action, style):
        super().__init__(label=label, style=style)
        self.cog, self.number, self.action = cog, number, action
    async def callback(self, interaction):
        await self.cog.edit_panel(interaction, self.number, self.action)


class TextEditModal(discord.ui.Modal):
    def __init__(self, cog, number, key, title, label, value):
        super().__init__(title=title)
        self.cog, self.number, self.key = cog, number, key
        self.field = discord.ui.TextInput(label=label[:45], default=value or "", max_length=100, required=key != "button_emoji")
        self.add_item(self.field)
    async def on_submit(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.number)
        panel[self.key] = self.field.value.strip() or None
        await self.cog.save_panel(interaction.guild.id, self.number, panel)
        await interaction.response.send_message("✅ تم حفظ التعديل.", ephemeral=True)


class ButtonSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=300)
        self.add_item(PanelAction(cog, number, "اسم زر التقديم", "button_name", discord.ButtonStyle.primary))
        self.add_item(PanelAction(cog, number, "إيموجي التقديم", "emoji", discord.ButtonStyle.primary))
        self.add_item(ColorMenu(cog, number))


class ColorMenu(discord.ui.Button):
    def __init__(self, cog, number):
        super().__init__(label="ألوان الزر", style=discord.ButtonStyle.primary)
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await interaction.response.send_message("اختر لون الزر:", view=ColorView(self.cog, self.number), ephemeral=True)


class ColorView(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=180)
        for label, value, style in [("أزرق", "primary", discord.ButtonStyle.primary), ("أخضر", "success", discord.ButtonStyle.success), ("رمادي", "secondary", discord.ButtonStyle.secondary), ("أحمر", "danger", discord.ButtonStyle.danger)]:
            self.add_item(ColorButton(cog, number, label, value, style))


class ColorButton(discord.ui.Button):
    def __init__(self, cog, number, label, value, style):
        super().__init__(label=label, style=style)
        self.cog, self.number, self.value = cog, number, value
    async def callback(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.number)
        panel["button_style"] = self.value
        await self.cog.save_panel(interaction.guild.id, self.number, panel)
        await interaction.response.send_message("✅ تم حفظ لون الزر.", ephemeral=True)


class QuestionSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=300)
        self.cog, self.number = cog, number
        for index in range(25):
            self.add_item(QuestionButton(cog, number, index))


class QuestionButton(discord.ui.Button):
    def __init__(self, cog, number, index):
        panel_label = f"السؤال {index + 1}"
        super().__init__(label=panel_label, style=discord.ButtonStyle.primary, row=index // 5)
        self.cog, self.number, self.index = cog, number, index
    async def callback(self, interaction):
        panel = await self.cog.get_panel(interaction.guild.id, self.number)
        questions = panel["questions"]
        current = questions[self.index] if self.index < len(questions) else {"label": "", "required": True, "paragraph": False}
        await interaction.response.send_modal(QuestionModal(self.cog, self.number, self.index, current))


class QuestionModal(discord.ui.Modal):
    def __init__(self, cog, number, index, current):
        super().__init__(title=f"تعديل السؤال {index + 1}")
        self.cog, self.number, self.index = cog, number, index
        self.text = discord.ui.TextInput(label="نص السؤال (حتى 45 حرفاً)", default=current.get("label", ""), max_length=45)
        self.required = discord.ui.TextInput(label="مطلوب؟ اكتب نعم أو لا", default="نعم" if current.get("required", True) else "لا", max_length=3)
        self.paragraph = discord.ui.TextInput(label="فقرة؟ اكتب نعم أو لا", default="نعم" if current.get("paragraph", False) else "لا", max_length=3)
        self.add_item(self.text); self.add_item(self.required); self.add_item(self.paragraph)
    async def on_submit(self, interaction):
        required = self.required.value.strip().lower() in {"نعم", "yes", "y", "1"}
        paragraph = self.paragraph.value.strip().lower() in {"نعم", "yes", "y", "1"}
        await self.cog.save_question(interaction, self.number, self.index, self.text.value, required, paragraph)


class ChannelPicker(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=180)
        self.add_item(ChannelSelect(cog, number))


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog, number):
        super().__init__(placeholder="اختر روم السجلات...", channel_types=[discord.ChannelType.text])
        self.cog, self.number = cog, number
    async def callback(self, interaction):
        await self.cog.select_results(interaction, self.number, self.values[0].id)


class RoleSettings(discord.ui.View):
    def __init__(self, cog, number):
        super().__init__(timeout=300)
        self.cog, self.number = cog, number
        self.add_item(RoleSelect(cog, number, "accept_role", "رتبة القبول"))
        self.add_item(RoleSelect(cog, number, "reject_role", "رتبة الرفض"))
        self.add_item(RemoveRoleButton(cog, number, "accept_role", "إزالة رتبة القبول"))
        self.add_item(RemoveRoleButton(cog, number, "reject_role", "إزالة رتبة الرفض"))


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog, number, key, placeholder):
        super().__init__(placeholder=placeholder)
        self.cog, self.number, self.key = cog, number, key
    async def callback(self, interaction):
        await self.cog.set_role(interaction, self.number, self.key, self.values[0].id)


class RemoveRoleButton(discord.ui.Button):
    def __init__(self, cog, number, key, label):
        super().__init__(label=label, style=discord.ButtonStyle.danger)
        self.cog, self.number, self.key = cog, number, key
    async def callback(self, interaction):
        await self.cog.set_role(interaction, self.number, self.key, None)


class PublishedApplication(discord.ui.View):
    def __init__(self, cog, guild_id, number, panel):
        super().__init__(timeout=None)
        emoji = panel.get("button_emoji") or "📝"
        self.add_item(
            StartButton(
                cog,
                guild_id,
                number,
                panel.get("button_label") or "ابدأ التقديم",
                panel.get("button_style", "primary"),
                emoji,
            )
        )


class StartButton(discord.ui.Button):
    def __init__(self, cog, guild_id, number, label, style, emoji):
        kwargs = {
            "label": label[:80],
            "style": STYLE_MAP.get(style, discord.ButtonStyle.primary),
            "custom_id": f"ader:application:start:{guild_id}:{number}",
        }
        if emoji:
            kwargs["emoji"] = emoji
        try:
            super().__init__(**kwargs)
        except (ValueError, TypeError):
            kwargs.pop("emoji", None)
            super().__init__(**kwargs)
        self.cog, self.guild_id, self.number = cog, guild_id, number
    async def callback(self, interaction):
        if interaction.guild_id != self.guild_id:
            return await interaction.response.send_message("هذه اللوحة مرتبطة بسيرفر آخر.", ephemeral=True)
        await self.cog.start_application(interaction, self.number)


class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, number, start, questions):
        super().__init__(title=f"تقديم {number} — الأسئلة {start + 1}-{min(start + 5, len(questions))}")
        self.cog, self.number, self.questions = cog, number, questions
        for question in questions[start:start + 5]:
            style = discord.TextStyle.paragraph if question.get("paragraph") else discord.TextStyle.short
            self.add_item(discord.ui.TextInput(label=question["label"][:45], required=bool(question.get("required", True)), style=style, max_length=4000))
    async def on_submit(self, interaction):
        await self.cog.collect(interaction, self.number, self.questions)


class ReviewView(discord.ui.View):
    def __init__(self, cog, application_id):
        super().__init__(timeout=None)
        self.add_item(ReviewButton(cog, application_id, True))
        self.add_item(ReviewButton(cog, application_id, False))


class ReviewButton(discord.ui.Button):
    def __init__(self, cog, application_id, accepted):
        action = "accept" if accepted else "reject"
        super().__init__(
            label="قبول" if accepted else "رفض",
            style=discord.ButtonStyle.success if accepted else discord.ButtonStyle.danger,
            emoji="✅" if accepted else "❌",
            custom_id=f"ader:application:review:{application_id}:{action}",
        )
        self.cog, self.application_id, self.accepted = cog, application_id, accepted
    async def callback(self, interaction):
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("❌ ما عندكش صلاحية مراجعة التقديمات.", ephemeral=True)
        if self.accepted:
            await self.cog.review(interaction, self.application_id, True)
        else:
            await interaction.response.send_modal(RejectModal(self.cog, self.application_id))


class RejectModal(discord.ui.Modal):
    def __init__(self, cog, application_id):
        super().__init__(title="سبب رفض التقديم")
        self.cog, self.application_id = cog, application_id
        self.reason = discord.ui.TextInput(label="سبب الرفض", style=discord.TextStyle.paragraph, required=False, max_length=1000)
        self.add_item(self.reason)
    async def on_submit(self, interaction):
        await self.cog.review(interaction, self.application_id, False, self.reason.value.strip() or None)


async def setup(bot: commands.Bot):
    await bot.add_cog(AppV4(bot))
