from __future__ import annotations

import io
from typing import Optional

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps


class ProfileCard(commands.Cog):
    def __init__(self, bot: commands.Bot, db, config: dict):
        self.bot = bot
        self.db = db
        self.config = config

    async def cog_load(self):
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS profile_reputation (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reputation INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )"""
        )
        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS profile_reputation_cooldowns (
                guild_id INTEGER NOT NULL,
                giver_id INTEGER NOT NULL,
                last_given_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, giver_id)
            )"""
        )

    @staticmethod
    def _font(size: int, bold: bool = False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
        ]
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
        return ImageFont.load_default()

    @classmethod
    def _fit_text(cls, draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, bold: bool = False):
        for size in range(start_size, 12, -2):
            font = cls._font(size, bold)
            if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
                return font
        return cls._font(14, bold)

    @staticmethod
    def _short_number(value: int) -> str:
        value = int(value)
        if abs(value) >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f}".rstrip("0").rstrip(".") + "B"
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
        if abs(value) >= 1_000:
            return f"{value / 1_000:.2f}".rstrip("0").rstrip(".") + "K"
        return f"{value:,}"

    @staticmethod
    def _circle_asset(raw: bytes, size: int) -> Image.Image:
        asset = Image.open(io.BytesIO(raw)).convert("RGBA")
        asset = ImageOps.fit(asset, (size, size), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        asset.putalpha(mask)
        return asset

    @classmethod
    async def _render(cls, member: discord.Member, guild: discord.Guild, data: dict, rank: int, reputation: int):
        width, height = 1200, 700
        image = Image.new("RGBA", (width, height), (8, 10, 22, 255))

        for cx, cy, radius, color, alpha in [
            (920, 55, 500, (24, 85, 170), 90),
            (585, 430, 520, (50, 58, 225), 110),
            (1080, 360, 540, (176, 48, 245), 100),
            (790, 700, 520, (115, 74, 255), 75),
        ]:
            glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow, "RGBA")
            gd.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=(*color, alpha))
            image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(90)))

        draw = ImageDraw.Draw(image, "RGBA")

        for i in range(9):
            x = 285 + i * 100
            draw.polygon(
                [(x, 255), (x+270, -35), (width+185, 235), (width+185, 395), (x+180, 330)],
                fill=(42+i*8, 72+i*6, 168+min(i*10, 80), 34),
            )

        for i in range(5):
            draw.rounded_rectangle(
                (318+i*82, 235+i*34, 1290+i*62, 745+i*15),
                radius=112,
                outline=(192, 128, 255, 32),
                width=38,
            )

        draw.rounded_rectangle((350, 590, 1280, 790), radius=110, fill=(181, 77, 247, 46))
        draw.rounded_rectangle((330, 625, 1270, 790), radius=100, fill=(28, 172, 255, 28))

        for row in range(6):
            for col in range(9):
                x = 1002 + col * 25 + (row % 2) * 10
                y = 560 + row * 22
                draw.ellipse((x, y, x+8, y+8), fill=(132, 116, 255, 44))

        draw.rounded_rectangle(
            (0, 0, 405, height),
            radius=55,
            fill=(157, 160, 169, 134),
            outline=(255, 255, 255, 30),
            width=2,
        )

        avatar_size = 275
        avatar_raw = await member.display_avatar.with_size(512).read()
        avatar = cls._circle_asset(avatar_raw, avatar_size)

        ring_size = avatar_size + 22
        ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((2, 2, ring_size-3, ring_size-3), outline=(224, 224, 250, 245), width=5)
        image.alpha_composite(ring, (53, 18))
        image.alpha_composite(avatar, (64, 29))

        server_size = 88
        server_x, server_y = 614, 8
        if guild.icon is not None:
            try:
                server_raw = await guild.icon.with_size(256).read()
                image.alpha_composite(cls._circle_asset(server_raw, server_size), (server_x, server_y))
                draw.ellipse(
                    (server_x-2, server_y-2, server_x+server_size+2, server_y+server_size+2),
                    outline=(0, 0, 0, 150),
                    width=4,
                )
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                pass
        else:
            draw.ellipse(
                (server_x, server_y, server_x+server_size, server_y+server_size),
                fill=(30, 28, 48, 245),
                outline=(170, 120, 255, 220),
                width=4,
            )
            draw.text(
                (server_x+server_size//2, server_y+server_size//2),
                guild.name[:1].upper() or "A",
                font=cls._font(34, True),
                fill=(245, 238, 255, 255),
                anchor="mm",
            )

        username = member.name[:28]
        draw.text((500, 188), username, font=cls._fit_text(draw, username, 620, 54, True), fill=(255, 255, 255, 255))

        stats = [
            ("LVL", str(max(0, int(data.get("level", 0))))),
            ("REP", f"{int(reputation):+d}"),
            ("ANORIS", cls._short_number(int(data.get("balance", 0)))),
            ("RANK", f"{rank:,}"),
        ]
        y = 405
        for label, value in stats:
            draw.text((78, y), label, font=cls._font(20), fill=(239, 239, 248, 255))
            draw.text((78, y+31), value, font=cls._font(34, True), fill=(255, 255, 255, 255))
            y += 70

        level = max(0, int(data.get("level", 0)))
        total_xp = max(0, int(data.get("xp", 0)))
        required = max(100, 100 + level * 150)
        current = total_xp % required
        progress = min(1.0, current / required) if required else 0.0

        bx1, by1, bx2, by2 = 500, 485, 1135, 545
        draw.rounded_rectangle((bx1, by1, bx2, by2), radius=30, fill=(236, 232, 255, 232), outline=(250, 247, 255, 180), width=2)
        fill_width = max(18, int((bx2-bx1) * progress))
        draw.rounded_rectangle((bx1, by1, bx1+fill_width, by2), radius=30, fill=(57, 57, 70, 255))
        draw.text(
            ((bx1+bx2)//2, (by1+by2)//2),
            f"{current:,} / {required:,}",
            font=cls._font(28),
            fill=(25, 25, 35, 255),
            anchor="mm",
        )
        draw.text(
            (817, 580),
            f"TOTAL XP: {total_xp:,}",
            font=cls._font(24, True),
            fill=(245, 241, 255, 255),
            anchor="mm",
        )

        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return output

    async def _send_profile(self, destination, member: discord.Member, guild: discord.Guild):
        data = await self.db.get_user(member.id, guild.id)
        if data is None:
            data = await self.db.create_user(member.id, guild.id)

        rep_row = await self.db.fetchone(
            "SELECT reputation FROM profile_reputation WHERE guild_id=? AND user_id=?",
            (guild.id, member.id),
        )
        reputation = int(rep_row["reputation"]) if rep_row else 0

        rank_row = await self.db.fetchone(
            "SELECT COUNT(*) + 1 AS rank FROM users WHERE guild_id=? AND xp>?",
            (guild.id, int(data.get("xp", 0))),
        )
        rank = int(rank_row["rank"]) if rank_row else 1

        card = await self._render(member, guild, data, rank, reputation)
        await destination.send(file=discord.File(card, filename="ader-profile.png"))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        parts = message.content.strip().split()
        if not parts or parts[0].lower() != "p":
            return

        if len(parts) > 2:
            await message.reply(
                "❌ الاستعمال: P أو P @العضو أو P ID",
                mention_author=False,
                delete_after=8,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        target = message.mentions[0] if message.mentions else None
        if target is None and len(parts) == 2:
            raw_target = parts[1].strip().strip("<@!>")
            if raw_target.isdigit():
                target = message.guild.get_member(int(raw_target))
                if target is None:
                    try:
                        target = await message.guild.fetch_member(int(raw_target))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        target = None

        if len(parts) == 2 and target is None:
            await message.reply(
                "❌ ما لقيتش هاد العضو. استعمل P @العضو أو P ID.",
                mention_author=False,
                delete_after=8,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await self._send_profile(message.channel, target or message.author, message.guild)

    @commands.hybrid_command(name="rep")
    async def reputation(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if ctx.guild is None:
            return

        if member is None:
            await ctx.send(
                "❌ الاستعمال: !rep @العضو/ID أو /rep ثم اختر العضو.",
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=8 if ctx.interaction is None else None,
            )
            return

        if member.bot:
            await ctx.send(
                "❌ لا يمكنك إعطاء السمعة للبوتات.",
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=8 if ctx.interaction is None else None,
            )
            return

        if member.id == ctx.author.id:
            await ctx.send(
                "❌ لا يمكنك إعطاء السمعة لنفسك.",
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=8 if ctx.interaction is None else None,
            )
            return

        now = discord.utils.utcnow().timestamp()
        cooldown_seconds = 24 * 60 * 60

        await self.db.execute(
            "INSERT OR IGNORE INTO profile_reputation_cooldowns(guild_id,giver_id,last_given_at) VALUES(?,?,0)",
            (ctx.guild.id, ctx.author.id),
        )

        row = await self.db.fetchone(
            "SELECT last_given_at FROM profile_reputation_cooldowns WHERE guild_id=? AND giver_id=?",
            (ctx.guild.id, ctx.author.id),
        )
        last_given_at = float(row["last_given_at"]) if row else 0.0
        remaining = cooldown_seconds - (now - last_given_at)

        if remaining > 0:
            hours, rem = divmod(int(remaining), 3600)
            minutes = rem // 60
            seconds = rem % 60
            await ctx.send(
                f"⏳ مازال خاصك تستنى {hours} ساعة و {minutes} دقيقة و {seconds} ثانية قبل ما تعطي سمعة أخرى.",
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=8 if ctx.interaction is None else None,
            )
            return

        claimed = await self.db.execute(
            """UPDATE profile_reputation_cooldowns
               SET last_given_at=?
               WHERE guild_id=? AND giver_id=? AND (? - last_given_at) >= ?""",
            (now, ctx.guild.id, ctx.author.id, now, cooldown_seconds),
        )

        if claimed.rowcount != 1:
            await ctx.send(
                "⏳ لا يمكنك إعطاء سمعة أخرى حالياً. حاول مرة أخرى بعد انتهاء المدة.",
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=8 if ctx.interaction is None else None,
            )
            return

        await self.db.execute(
            """INSERT INTO profile_reputation(guild_id,user_id,reputation)
               VALUES(?,?,1)
               ON CONFLICT(guild_id,user_id)
               DO UPDATE SET reputation=reputation+1""",
            (ctx.guild.id, member.id),
        )

        row = await self.db.fetchone(
            "SELECT reputation FROM profile_reputation WHERE guild_id=? AND user_id=?",
            (ctx.guild.id, member.id),
        )
        reputation = int(row["reputation"]) if row else 1

        await ctx.send(
            f"✅ {ctx.author.mention} عطى +1 REP لـ {member.mention}.\n"
            f"📈 السمعة الحالية ديال {member.display_name}: {reputation:+d} REP",
            allowed_mentions=discord.AllowedMentions(users=[ctx.author, member]),
        )

    @commands.command(name="p")
    async def profile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if ctx.guild is None:
            return
        await self._send_profile(ctx, member or ctx.author, ctx.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCard(bot, bot.db, bot.config))
