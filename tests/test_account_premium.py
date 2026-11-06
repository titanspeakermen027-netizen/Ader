"""Tests for account Premium and user-linked bot persistence."""

import asyncio
import time
from pathlib import Path

import pytest

from cogs.server_premium import parse_duration
from database.db_manager import DatabaseManager


def run(coro):
    return asyncio.run(coro)


def test_parse_user_premium_durations():
    assert parse_duration("1y") == 365 * 86400
    assert parse_duration("2mo") == 60 * 86400
    assert parse_duration("7d") == 7 * 86400
    assert parse_duration("12h") == 12 * 3600
    assert parse_duration("30m") == 30 * 60
    assert parse_duration("15s") == 15

    with pytest.raises(ValueError):
        parse_duration("0d")
    with pytest.raises(ValueError):
        parse_duration("12months")
    with pytest.raises(ValueError):
        parse_duration("11y")


def test_user_premium_grant_extend_and_expire(tmp_path: Path):
    async def scenario():
        db = DatabaseManager(str(tmp_path / "account-premium.sqlite3"))
        await db.connect()
        try:
            now = time.time()
            record = await db.set_user_premium(
                user_id=1001,
                started_at=now,
                expires_at=now + 3600,
                granted_by=2002,
            )
            assert record is not None
            assert record["user_id"] == 1001
            assert record["granted_by"] == 2002
            assert await db.is_user_premium(1001)

            extended = await db.set_user_premium(
                user_id=1001,
                started_at=now,
                expires_at=now + 7200,
                granted_by=2002,
            )
            assert extended["expires_at"] == pytest.approx(now + 7200)
            assert extended["started_at"] == pytest.approx(now)

            await db.set_user_premium(
                user_id=1002,
                started_at=now - 7200,
                expires_at=now - 1,
                granted_by=2002,
            )
            assert await db.get_user_premium(1002) is None
            assert await db.fetchone("SELECT user_id FROM user_premium WHERE user_id=?", (1002,)) is None

            await db.remove_user_premium(1001)
            assert not await db.is_user_premium(1001)
        finally:
            await db.disconnect()

    run(scenario())


def test_linked_bot_account_allowlist_and_profile(tmp_path: Path):
    async def scenario():
        db = DatabaseManager(str(tmp_path / "linked-account.sqlite3"))
        await db.connect()
        try:
            record = await db.save_linked_bot_account(
                owner_id=1001,
                encrypted_token="encrypted-token",
                token_fingerprint="fingerprint-1",
                allowed_guild_ids=[30, 10, 30, 20],
                profile={
                    "bot_name": "Account Bot",
                    "activity_text": "Testing",
                    "activity_type": "playing",
                },
            )
            assert record["owner_id"] == 1001
            assert record["allowed_guild_ids"] == [10, 20, 30]
            assert record["bot_name"] == "Account Bot"

            by_fingerprint = await db.get_linked_bot_account_by_fingerprint("fingerprint-1")
            assert by_fingerprint is not None
            assert by_fingerprint["owner_id"] == 1001

            await db.update_linked_bot_account_guilds(1001, [40, 20, 40])
            await db.update_linked_bot_account_profile(
                1001,
                {"bot_name": "Updated Bot", "activity_type": "watching"},
            )
            await db.update_linked_bot_account_runtime(1001, 5555)
            await db.update_linked_bot_account_status(1001, "online")

            updated = await db.get_linked_bot_account(1001)
            assert updated["allowed_guild_ids"] == [20, 40]
            assert updated["bot_name"] == "Updated Bot"
            assert updated["activity_type"] == "watching"
            assert updated["bot_user_id"] == 5555
            assert updated["status"] == "online"

            assert len(await db.list_linked_bot_accounts()) == 1
            await db.delete_linked_bot_account(1001)
            assert await db.get_linked_bot_account(1001) is None
        finally:
            await db.disconnect()

    run(scenario())
