from __future__ import annotations

import io
from typing import Optional

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter


class ProfileCard(commands.Cog):
    """Ader profile card: P / P @member / P user_id."""

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

    @staticmethod
    def _font(size: int, bold: bool = False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf"
            if bold else "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
        ]
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

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
    def _rounded_avatar(raw: bytes, size: int) -> Image.Image:
        avatar = Image.open(io.BytesIO(raw)).convert("RGBA")
        avatar.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(avatar, ((size - avatar.width) // 2, (size - avatar.height) // 2))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        canvas.putalpha(mask)
        return canvas

    @classmethod
    async def _render(
        cls,
        member: discord.Member,
        data: dict,
        rank: int,
        reputation: int,
    ) -> io.BytesIO:
        width, height = 1200, 700
        image = Image.new("RGBA", (width, height), (12, 13, 25, 255))

        # Ader's purple/blue abstract card background.
        for cx, cy, radius, alpha in [
            (970, 80, 520, 50),
            (760, 350, 500, 42),
            (1120, 560, 430, 38),
        ]:
            glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow, "RGBA")
            gd.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=(105, 55, 230, alpha),
            )
            glow = glow.filter(ImageFilter.GaussianBlur(90))
            image = Image.alpha_composite(image, glow)

        draw = ImageDraw.Draw(image, "RGBA")

        for i in range(6):
            x = 315 + i * 110
            draw.polygon(
                [
                    (x, 245),
                    (x + 260, 60),
                    (width + 120, 245),
                    (width + 120, 410),
                    (x + 185, 330),
                ],
                fill=(45 + i * 12, 60, 190 + min(i * 10, 50), 42),
            )

        for i in range(4):
            draw.rounded_rectangle(
                (330 + i * 110, 210 + i * 35, 1280 + i * 75, 690 + i * 15),
                radius=100,
                outline=(155, 110, 255, 32),
                width=32,
            )

        # Left profile panel.
        draw.rounded_rectangle(
            (0, 0, 395, height),
            radius=52,
            fill=(37, 39, 51, 215),
            outline=(255, 255, 255, 22),
            width=2,
        )

        avatar_size = 270
        avatar_raw = await member.display_avatar.with_size(512).read()
        avatar = cls._rounded_avatar(avatar_raw, avatar_size)

        ring = Image.new("RGBA", (avatar_size + 28, avatar_size + 28), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse(
            (2, 2, avatar_size + 25, avatar_size + 25),
            outline=(183, 105, 255, 235),
            width=12,
        )
        image.alpha_composite(ring, (49, 25))
        image.alpha_composite(avatar, (63, 39))

        display_name = member.display_name or member.name
        if len(display_name) > 18:
            display_name = display_name[:17] + "…"

        draw.text(
            (198, 345),
            display_name,
            font=cls._font(34, True),
            fill=(250, 250, 255, 255),
            anchor="mm",
        )
        draw.text(
            (198, 386),
            "@" + member.name[:24],
            font=cls._font(20),
            fill=(195, 195, 215, 255),
            anchor="mm",
        )

        stats = [
            ("LVL", str(max(0, int(data.get("level", 0))))),
            ("REP", f"{int(reputation):+d}"),
            ("ANORIS", cls._short_number(int(data.get("balance", 0)))),
            ("RANK", f"{rank:,}"),
        ]

        y = 442
        for label, value in stats:
            draw.text((78, y), label, font=cls._font(17), fill=(220, 220, 235, 255))
            draw.text(
                (78, y + 28),
                value,
                font=cls._font(29, True),
                fill=(255, 255, 255, 255),
            )
            y += 62

        # Main profile area.
        draw.text(
            (470, 108),
            display_name,
            font=cls._font(54, True),
            fill=(255, 255, 255, 255),
        )
        draw.text(
            (472, 164),
            "@" + member.name,
            font=cls._font(22),
            fill=(190, 188, 215, 255),
        )

        level = max(0, int(data.get("level", 0)))
        total_xp = max(0, int(data.get("xp", 0)))

        # The stored XP and level stay authoritative. This only calculates
        # the visual progress bar without changing the database.
        required = max(100, 100 + level * 150)
        current = total_xp % required
        progress = min(1.0, current / required)

        bx1, by1, bx2, by2 = 470, 475, 1120, 535
        draw.rounded_rectangle(
            (bx1, by1, bx2, by2),
            radius=30,
            fill=(235, 231, 255, 225),
        )
        fill_x = bx1 + max(20, int((bx2 - bx1) * progress))
        draw.rounded_rectangle(
            (bx1, by1, fill_x, by2),
            radius=30,
            fill=(63, 61, 78, 255),
        )
        draw.text(
            ((bx1 + bx2) // 2, (by1 + by2) // 2),
            f"{current:,} / {required:,}",
            font=cls._font(25),
            fill=(20, 20, 30, 255),
            anchor="mm",
        )
        draw.text(
            (795, 568),
            f"TOTAL XP: {total_xp:,}",
            font=cls._font(22, True),
            fill=(235, 230, 255, 255),
            anchor="mm",
        )

        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return output

    @commands.command(name="p", aliases=["P"])
    async def profile(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ):
        """P -> own profile, P @user/id -> another member profile."""
        if ctx.guild is None:
            return

        target = member or ctx.author
        data = await self.db.get_user(target.id, ctx.guild.id)
        if data is None:
            data = await self.db.create_user(target.id, ctx.guild.id)

        rep_row = await self.db.fetchone(
            "SELECT reputation FROM profile_reputation WHERE guild_id=? AND user_id=?",
            (ctx.guild.id, target.id),
        )
        reputation = int(rep_row["reputation"]) if rep_row else 0

        rank_row = await self.db.fetchone(
            "SELECT COUNT(*) + 1 AS rank FROM users WHERE guild_id=? AND xp>?",
            (ctx.guild.id, int(data.get("xp", 0))),
        )
        rank = int(rank_row["rank"]) if rank_row else 1

        card = await self._render(target, data, rank, reputation)
        await ctx.send(file=discord.File(card, filename="ader-profile.png"))


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCard(bot, bot.db, bot.config))
