from cogs.professional_core import normalize_reaction_emoji, normalize_reaction_emojis


def test_normalize_unicode_reaction():
    assert normalize_reaction_emoji("🔥") == "🔥"


def test_normalize_custom_reaction():
    assert normalize_reaction_emoji("<:party:123456789012345678>") == "<:party:123456789012345678>"
    assert normalize_reaction_emoji("<a:party:123456789012345678>") == "<a:party:123456789012345678>"


def test_plain_emoji_name_is_rejected():
    assert normalize_reaction_emoji("fire") is None
    assert normalize_reaction_emoji("smile") is None


def test_duplicate_reactions_are_skipped():
    assert normalize_reaction_emojis(["🔥", "🔥", "<:party:123456789>", "<:party:123456789>"]) == [
        "🔥",
        "<:party:123456789>",
    ]


def test_comma_separated_reactions_are_supported():
    assert normalize_reaction_emojis("😀, 😡, 😀") == ["😀", "😡"]
