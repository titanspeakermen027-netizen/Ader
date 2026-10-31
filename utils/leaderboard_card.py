from __future__ import annotations

import io
import os
from typing import Any

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

try:
    from pilmoji import Pilmoji
except ImportError:  # Optional fallback if dependency installation is incomplete.
    Pilmoji = None


WIDTH = 1400
HEIGHT = 1180

_MATH_FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/stixtwomath/STIXTwoMath-Regular.ttf"
_SPECIAL_FONT_URLS = {
    "lao": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanslao/NotoSansLao%5Bwdth,wght%5D.ttf",
    "japanese": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf",
    "symbols": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssymbols2/NotoSansSymbols2-Regular.ttf",
}
_SPECIAL_FONT_MAX_BYTES = {
    "lao": 2_000_000,
    "japanese": 15_000_000,
    "symbols": 3_000_000,
}
_SPECIAL_FONT_BYTES: dict[str, bytes] = {}
_SPECIAL_FONT_DOWNLOAD_ATTEMPTED: set[str] = set()
_MATH_FONT_BYTES: bytes | None = None
_MATH_FONT_DOWNLOAD_ATTEMPTED = False

PALETTE = {
    "background_top": (11, 10, 20),
    "background_bottom": (18, 14, 31),
    "panel": (24, 21, 39, 238),
    "panel_soft": (29, 25, 46, 232),
    "purple": (151, 104, 245, 255),
    "purple_light": (198, 166, 255, 255),
    "purple_dark": (91, 57, 160, 255),
    "text": (244, 241, 250, 255),
    "muted": (166, 159, 185, 255),
    "silver": (180, 190, 211, 255),
    "bronze": (190, 133, 105, 255),
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _is_arabic(text: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f" for char in text)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, ...],
    anchor: str = "mm",
) -> None:
    options: dict[str, Any] = {"font": font, "fill": fill, "anchor": anchor}
    if _is_arabic(text):
        options.update({"direction": "rtl", "language": "ar"})
    try:
        draw.text(position, text, **options)
    except (TypeError, ValueError):
        options.pop("direction", None)
        options.pop("language", None)
        draw.text(position, text, **options)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = True):
    for candidate_size in range(size, 13, -2):
        font = _font(candidate_size, bold)
        try:
            box = draw.textbbox((0, 0), text, font=font, direction="rtl" if _is_arabic(text) else "ltr")
        except (TypeError, ValueError):
            box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
    return _font(14, bold)


async def _ensure_math_font() -> None:
    """Load a math-capable font on demand if the host does not provide one."""
    global _MATH_FONT_BYTES, _MATH_FONT_DOWNLOAD_ATTEMPTED

    if _MATH_FONT_DOWNLOAD_ATTEMPTED:
        return
    _MATH_FONT_DOWNLOAD_ATTEMPTED = True

    # Always fetch STIX Two Math: it explicitly includes Mathematical Alphanumeric
    # Symbols (U+1D400–U+1D7FF), unlike many general-purpose system fonts.

    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_MATH_FONT_URL) as response:
                if response.status != 200:
                    return
                data = await response.content.read(4_000_001)
                if not 20_000 <= len(data) <= 4_000_000:
                    return
                # Validate the downloaded font before keeping it in memory.
                ImageFont.truetype(io.BytesIO(data), 20)
                _MATH_FONT_BYTES = data
    except Exception:
        # Offline hosts still use any suitable fonts already installed.
        return


def _has_styled_unicode(text: str) -> bool:
    # Mathematical alphanumeric symbols are used by names such as 𝑉𝐸𝑆𝑃𝐸𝑅.
    return any(
        0x1D400 <= ord(char) <= 0x1D7FF
        or 0x2100 <= ord(char) <= 0x214F
        or 0x2460 <= ord(char) <= 0x24FF
        for char in text
    )


def _fit_name_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int):
    if not _has_styled_unicode(text):
        return _fit_font(draw, text, max_width, size)

    # Prefer STIX Two Math because it contains the mathematical styled alphabets
    # used in names such as 𝑉𝐸𝑆𝑃𝐸𝑅.
    if _MATH_FONT_BYTES:
        for candidate_size in range(size, 11, -1):
            try:
                font = ImageFont.truetype(io.BytesIO(_MATH_FONT_BYTES), candidate_size)
                direction = "rtl" if _is_arabic(text) else "ltr"
                box = draw.textbbox((0, 0), text, font=font, direction=direction)
                if box[2] - box[0] <= max_width:
                    return font
            except (OSError, TypeError, ValueError):
                continue

async def _ensure_special_fonts(texts: list[str]) -> None:
    """Download only the script-specific fonts required by visible leaderboard names."""
    needed = set().union(*(_special_font_families(text) for text in texts)) if texts else set()
    needed = {family for family in needed if family in _SPECIAL_FONT_URLS}
    pending = needed - _SPECIAL_FONT_DOWNLOAD_ATTEMPTED
    if not pending:
        return

    _SPECIAL_FONT_DOWNLOAD_ATTEMPTED.update(pending)
    timeout = aiohttp.ClientTimeout(total=18, connect=7)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for family in pending:
                try:
                    async with session.get(_SPECIAL_FONT_URLS[family]) as response:
                        if response.status != 200:
                            continue
                        data = await response.content.read(_SPECIAL_FONT_MAX_BYTES[family] + 1)
                        if not 20_000 <= len(data) <= _SPECIAL_FONT_MAX_BYTES[family]:
                            continue
                        ImageFont.truetype(io.BytesIO(data), 18)
                        _SPECIAL_FONT_BYTES[family] = data
                except Exception:
                    continue
    except Exception:
        return


def _special_font_families(text: str) -> set[str]:
    """Return the fallback font families needed for the characters in a name."""
    families: set[str] = set()
    for char in text:
        code = ord(char)
        if (
            0x1D400 <= code <= 0x1D7FF
            or 0x2100 <= code <= 0x214F
            or 0x2460 <= code <= 0x24FF
        ):
            families.add("math")
        elif 0x0E80 <= code <= 0x0EFF:
            families.add("lao")
        elif (
            0x3000 <= code <= 0x30FF
            or 0x31F0 <= code <= 0x31FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xFF00 <= code <= 0xFFEF
        ):
            families.add("japanese")
        elif (
            0x2000 <= code <= 0x2BFF
            or 0x2E00 <= code <= 0x2E7F
            or 0x1D300 <= code <= 0x1D37F
        ):
            families.add("symbols")
    return families


def _name_font_for_family(family: str | None, size: int):
    if family == "math" and _MATH_FONT_BYTES:
        try:
            return ImageFont.truetype(io.BytesIO(_MATH_FONT_BYTES), size)
        except OSError:
            pass

    font_data = _SPECIAL_FONT_BYTES.get(family or "")
    if font_data:
        try:
            return ImageFont.truetype(io.BytesIO(font_data), size)
        except OSError:
            pass

    return _font(size, True)


def _name_runs(text: str) -> list[tuple[str | None, str]]:
    """Split a left-to-right name into runs that can use the right Unicode font."""
    runs: list[tuple[str | None, str]] = []
    current_family: str | None = None
    current_text = ""

    for char in text:
        code = ord(char)
        if code in (0xFE0E, 0xFE0F, 0x200D, 0x20E3) or 0x1F3FB <= code <= 0x1F3FF:
            family = current_family
        else:
            family = next(iter(_special_font_families(char)), None)

        if current_text and family != current_family:
            runs.append((current_family, current_text))
            current_text = ""
        current_family = family
        current_text += char

    if current_text:
        runs.append((current_family, current_text))
    return runs


def _draw_decorated_ltr_name(
    image: Image.Image,
    center: tuple[int, int],
    text: str,
    max_width: int,
    size: int,
) -> bool:
    """Draw mixed-script LTR names with script-specific fonts and Pilmoji."""
    draw = ImageDraw.Draw(image, "RGBA")
    runs = _name_runs(text)
    if not any(family is not None for family, _ in runs):
        return False

    chosen: list[tuple[str | None, str, Any, float]] = []
    total_width = 0.0
    for candidate_size in range(size, 10, -1):
        candidate_runs = []
        width_sum = 0.0
        for family, run_text in runs:
            font = _name_font_for_family(family, candidate_size)
            try:
                run_width = float(font.getlength(run_text))
            except (AttributeError, TypeError, ValueError):
                box = draw.textbbox((0, 0), run_text, font=font)
                run_width = float(box[2] - box[0])
            candidate_runs.append((family, run_text, font, run_width))
            width_sum += run_width
        chosen = candidate_runs
        total_width = width_sum
        if total_width <= max_width:
            break

    if not chosen:
        return False

    start_x = center[0] - total_width / 2

    def paint_runs(renderer=None):
        x = start_x
        for family, run_text, font, run_width in chosen:
            box = draw.textbbox((0, 0), run_text, font=font)
            run_y = round(center[1] - ((box[1] + box[3]) / 2))
            run_x = round(x - box[0])
            if renderer is not None:
                try:
                    renderer.text(
                        (run_x, run_y),
                        run_text,
                        fill=PALETTE["text"],
                        font=font,
                        direction="ltr",
                        language="en",
                    )
                except TypeError:
                    renderer.text((run_x, run_y), run_text, fill=PALETTE["text"], font=font)
            else:
                draw.text((run_x, run_y), run_text, font=font, fill=PALETTE["text"])
            x += run_width

    if Pilmoji is not None:
        try:
            with Pilmoji(image) as renderer:
                paint_runs(renderer)
            return True
        except Exception:
            # Use monochrome glyphs as a fallback if the emoji renderer is unavailable.
            paint_runs()
            return True

    paint_runs()
    return True


    # If the dedicated font could not be downloaded, try local math fonts.
    math_font_paths = [
        "/usr/share/fonts/opentype/stix2/STIXTwoMath-Regular.otf",
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
        "/usr/share/fonts/opentype/asana-math/Asana-Math.otf",
    ]
    for candidate_size in range(size, 11, -1):
        for path in math_font_paths:
            try:
                font = ImageFont.truetype(path, candidate_size)
                direction = "rtl" if _is_arabic(text) else "ltr"
                box = draw.textbbox((0, 0), text, font=font, direction=direction)
                if box[2] - box[0] <= max_width:
                    return font
            except (OSError, TypeError, ValueError):
                continue

    return _fit_font(draw, text, max_width, size)


def _draw_name(
    image: Image.Image,
    center: tuple[int, int],
    text: str,
    max_width: int,
    size: int,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")

    # Pillow has no automatic per-character font fallback. Preserve Arabic shaping
    # through the existing path and use script-specific runs for other Unicode names.
    if not _is_arabic(text) and _draw_decorated_ltr_name(image, center, text, max_width, size):
        return

    font = _fit_name_font(draw, text, max_width, size)
    direction = "rtl" if _is_arabic(text) else "ltr"

    try:
        box = draw.textbbox((0, 0), text, font=font, direction=direction)
    except (TypeError, ValueError):
        box = draw.textbbox((0, 0), text, font=font)

    # Pilmoji draws emoji as images; calculate a centered top-left position.
    x = round(center[0] - ((box[0] + box[2]) / 2))
    y = round(center[1] - ((box[1] + box[3]) / 2))

    if Pilmoji is not None:
        try:
            with Pilmoji(image) as emoji_renderer:
                try:
                    emoji_renderer.text(
                        (x, y),
                        text,
                        fill=PALETTE["text"],
                        font=font,
                        direction=direction,
                        language="ar" if direction == "rtl" else "en",
                    )
                except TypeError:
                    # Compatibility fallback for older Pillow/Pilmoji combinations.
                    emoji_renderer.text((x, y), text, fill=PALETTE["text"], font=font)
            return
        except Exception:
            # An unavailable emoji CDN must not prevent the leaderboard from rendering.
            pass

    _draw_text(draw, center, text, font, PALETTE["text"], anchor="mm")


def _round_avatar(raw: bytes | None, size: int) -> Image.Image:
    if raw:
        try:
            avatar = Image.open(io.BytesIO(raw)).convert("RGBA")
            avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
            # Darken gently so the avatars blend with the calm, low-glare design.
            rgb = ImageEnhance.Brightness(avatar.convert("RGB")).enhance(0.78)
            avatar = rgb.convert("RGBA")
        except (OSError, ValueError):
            avatar = Image.new("RGBA", (size, size), (53, 43, 75, 255))
    else:
        avatar = Image.new("RGBA", (size, size), (53, 43, 75, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    avatar.putalpha(mask)
    return avatar


def _crown_asset() -> Image.Image:
    size = 104
    crown = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.polygon(
        [(12, 60), (19, 23), (39, 42), (52, 10), (67, 42), (86, 23), (93, 60)],
        fill=(142, 92, 238, 110),
    )
    crown.alpha_composite(glow.filter(ImageFilter.GaussianBlur(11)))
    draw = ImageDraw.Draw(crown)
    points = [(12, 58), (19, 23), (39, 42), (52, 10), (67, 42), (86, 23), (93, 58)]
    draw.polygon(points, fill=(139, 91, 226, 255), outline=(220, 199, 255, 255))
    draw.rounded_rectangle((13, 59, 92, 74), radius=6, fill=(104, 66, 183, 255), outline=(211, 185, 255, 255), width=2)
    draw.ellipse((14, 17, 25, 28), fill=(221, 204, 255, 255))
    draw.ellipse((46, 4, 58, 16), fill=(242, 230, 255, 255))
    draw.ellipse((81, 17, 92, 28), fill=(221, 204, 255, 255))
    draw.ellipse((48, 61, 58, 71), fill=(224, 205, 255, 255))
    return crown


def _draw_avatar(
    image: Image.Image,
    avatar: Image.Image,
    center: tuple[int, int],
    rank: int,
    size: int,
) -> None:
    x = center[0] - size // 2
    y = center[1] - size // 2
    draw = ImageDraw.Draw(image, "RGBA")

    ring_color = PALETTE["purple_light"] if rank == 1 else PALETTE["silver"] if rank == 2 else PALETTE["bronze"]
    ring = Image.new("RGBA", (size + 24, size + 24), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring)
    ring_draw.ellipse((2, 2, size + 21, size + 21), fill=(16, 13, 28, 255), outline=ring_color, width=5)
    ring_draw.ellipse((8, 8, size + 15, size + 15), outline=(255, 255, 255, 38), width=2)
    image.alpha_composite(ring, (x - 12, y - 12))
    image.alpha_composite(avatar, (x, y))

    if rank == 1:
        crown = _crown_asset()
        image.alpha_composite(crown, (x - 32, y - 40))


def _draw_podium_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    rank: int,
    name: str,
    balance: int,
    avatar: Image.Image,
    center_x: int,
    top: int,
    is_first: bool = False,
) -> None:
    card_width = 410 if is_first else 330
    card_height = 420 if is_first else 360
    x1 = center_x - card_width // 2
    y1 = top
    x2 = center_x + card_width // 2
    y2 = top + card_height

    line = PALETTE["purple"] if rank == 1 else PALETTE["silver"] if rank == 2 else PALETTE["bronze"]
    draw.rounded_rectangle((x1, y1, x2, y2), radius=32, fill=PALETTE["panel"], outline=(*line[:3], 150), width=3 if is_first else 2)
    draw.rounded_rectangle((x1 + 13, y1 + 13, x2 - 13, y2 - 13), radius=25, outline=(255, 255, 255, 19), width=1)

    avatar_size = 190 if is_first else 146
    avatar_center = (center_x, y1 + (avatar_size // 2) + (78 if is_first else 70))
    _draw_avatar(image, avatar, avatar_center, rank, avatar_size)

    badge_y = y1 + 30
    draw.rounded_rectangle(
        (center_x - 42, badge_y - 18, center_x + 42, badge_y + 18),
        radius=18,
        fill=(*line[:3], 34),
        outline=(*line[:3], 175),
        width=2,
    )
    _draw_text(draw, (center_x, badge_y), f"#{rank}", _font(21 if is_first else 19, True), PALETTE["text"])

    name_y = avatar_center[1] + avatar_size // 2 + 37
    _draw_name(image, (center_x, name_y), name, card_width - 48, 27 if is_first else 23)
    balance_y = name_y + 43
    _draw_text(draw, (center_x, balance_y), f"{balance:,} ANORIS", _fit_font(draw, f"{balance:,} ANORIS", card_width - 48, 24 if is_first else 21), PALETTE["purple_light"])


async def _get_member_data(bot: discord.Client, guild: discord.Guild, row: Any) -> tuple[str, int, bytes | None]:
    user_id = int(row["user_id"])
    balance = int(row["balance"])
    user: discord.User | discord.Member | None = guild.get_member(user_id) or bot.get_user(user_id)

    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None

    # Prefer the account's global display name; never use a server-specific nickname.
    name = getattr(user, "global_name", None) or getattr(user, "name", None)
    name = str(name or f"عضو {user_id}")

    raw = None
    if user is not None:
        try:
            raw = await user.display_avatar.with_size(256).read()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, OSError):
            raw = None

    return name, balance, raw


async def render_richest_leaderboard(
    bot: discord.Client,
    guild: discord.Guild,
    rows: list[Any],
    currency_name: str = "ANORIS",
) -> io.BytesIO:
    """Render a dark, purple, image-based global ANORIS top-ten leaderboard."""
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))

    # A subdued vertical gradient with soft purple ambient light.
    draw = ImageDraw.Draw(image, "RGBA")
    top_color = PALETTE["background_top"]
    bottom_color = PALETTE["background_bottom"]
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)
        color = tuple(int(top_color[i] * (1 - ratio) + bottom_color[i] * ratio) for i in range(3))
        draw.line((0, y, WIDTH, y), fill=(*color, 255))

    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((390, 80, 1030, 690), fill=(111, 65, 190, 42))
    glow_draw.ellipse((40, 690, 640, 1160), fill=(70, 48, 125, 24))
    glow_draw.ellipse((770, 670, 1370, 1160), fill=(87, 50, 145, 24))
    image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(105)))
    draw = ImageDraw.Draw(image, "RGBA")

    _draw_text(draw, (WIDTH // 2, 76), f"أغنى أعضاء {currency_name}", _font(48, True), PALETTE["text"])
    _draw_text(draw, (WIDTH // 2, 133), "الترتيب العالمي لأصحاب أعلى الأرصدة", _font(22), PALETTE["muted"])
    draw.rounded_rectangle((465, 171, 935, 175), radius=2, fill=(148, 105, 223, 145))

    entries = []
    for row in rows[:10]:
        entries.append(await _get_member_data(bot, guild, row))

    if any(_has_styled_unicode(name) for name, _, _ in entries):
        await _ensure_math_font()
    await _ensure_special_fonts([name for name, _, _ in entries if not _is_arabic(name)])

    # Top three: the winner is deliberately larger and slightly higher.
    positions = {
        1: (700, 205, True),
        2: (330, 270, False),
        3: (1070, 270, False),
    }
    for rank, (center_x, top, is_first) in positions.items():
        if len(entries) < rank:
            continue
        name, balance, raw = entries[rank - 1]
        avatar_size = 190 if rank == 1 else 146
        avatar = _round_avatar(raw, avatar_size)
        _draw_podium_card(image, draw, rank, name, balance, avatar, center_x, top, is_first)

    # Positions 4–10 are shown in two calm, compact columns.
    remaining = entries[3:]
    row_height = 88
    row_gap = 16
    column_width = 620
    column_x = (70, 710)

    for offset, (name, balance, raw) in enumerate(remaining):
        column = 0 if offset < 4 else 1
        row_index = offset if offset < 4 else offset - 4
        x = column_x[column]
        y = 690 + row_index * (row_height + row_gap)
        x2 = x + column_width
        draw.rounded_rectangle(
            (x, y, x2, y + row_height),
            radius=22,
            fill=PALETTE["panel_soft"],
            outline=(153, 119, 204, 58),
            width=2,
        )

        avatar_size = 54
        avatar = _round_avatar(raw, avatar_size)
        avatar_x = x2 - 62
        avatar_y = y + row_height // 2
        draw.ellipse(
            (avatar_x - 31, avatar_y - 31, avatar_x + 31, avatar_y + 31),
            outline=(151, 104, 245, 155),
            width=2,
        )
        image.alpha_composite(avatar, (avatar_x - avatar_size // 2, avatar_y - avatar_size // 2))

        rank = offset + 4
        draw.rounded_rectangle(
            (x2 - 188, y + 24, x2 - 145, y + 64),
            radius=13,
            fill=(123, 85, 183, 58),
            outline=(166, 128, 220, 100),
            width=1,
        )
        _draw_text(draw, (x2 - 166, avatar_y), f"#{rank}", _font(17, True), PALETTE["purple_light"])
        _draw_name(image, (x + 300, y + 31), name, 260, 22)
        _draw_text(draw, (x + 300, y + 62), f"{balance:,} ANORIS", _fit_font(draw, f"{balance:,} ANORIS", 260, 18), PALETTE["muted"])

    draw = ImageDraw.Draw(image, "RGBA")
    draw.line((70, 1140, WIDTH - 70, 1140), fill=(157, 126, 205, 55), width=1)
    _draw_text(draw, (WIDTH // 2, 1158), "ANO BOT  •  ANORIS GLOBAL RANKING", _font(15, True), (148, 139, 168, 255))

    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
