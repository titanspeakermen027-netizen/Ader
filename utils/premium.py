"""Ader Premium plans, entitlements and feature gates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PremiumFeature:
    key: str
    name: str
    description: str
    free_limit: int | None = None
    premium_limit: int | None = None


PLAN_ID = "premium"

FEATURES: tuple[PremiumFeature, ...] = (
    PremiumFeature("dashboard_advanced", "Advanced Dashboard", "لوحة تحكم Premium المتقدمة."),
    PremiumFeature("ticket_panels", "Ticket Panels", "لوحات تذاكر متعددة وقابلة للتخصيص.", free_limit=1, premium_limit=None),
    PremiumFeature("analytics_history", "Analytics History", "احتفاظ أطول ببيانات الإحصائيات.", free_limit=7, premium_limit=365),
    PremiumFeature("custom_bot_identity", "Custom Bot Identity", "تخصيص اسم وصورة وبنر وحالة البوت المرتبط."),
    PremiumFeature("linked_bot", "Linked Bot", "ربط بوت Discord خاص بالسيرفر مع نفس قاعدة البيانات."),
    PremiumFeature("advanced_automod", "Advanced AutoMod", "قواعد حماية وفلترة متقدمة.", free_limit=1, premium_limit=None),
    PremiumFeature("automation", "Automation", "أتمتة متقدمة لإجراءات السيرفر."),
    PremiumFeature("branding", "Premium Branding", "تخصيص واجهات ورسائل Ader."),
)


def feature_map() -> dict[str, PremiumFeature]:
    return {item.key: item for item in FEATURES}


async def active_record(db, guild_id: int) -> dict[str, Any] | None:
    if not hasattr(db, "get_server_premium"):
        return None
    return await db.get_server_premium(guild_id)


async def is_active(db, guild_id: int) -> bool:
    return bool(await active_record(db, guild_id))


async def has_feature(db, guild_id: int, feature: str) -> bool:
    record = await active_record(db, guild_id)
    if not record:
        return False
    plan_id = str(record.get("plan_id") or PLAN_ID)
    if plan_id not in {"premium", "premium_plus"}:
        return False
    if hasattr(db, "get_premium_entitlement"):
        override = await db.get_premium_entitlement(guild_id, feature)
        if override is not None:
            return bool(override.get("enabled"))
    return feature in feature_map()


async def limit_for(db, guild_id: int, feature: str) -> int | None:
    record = await active_record(db, guild_id)
    premium = bool(record)
    item = feature_map().get(feature)
    if item is None:
        return 0
    if premium:
        if hasattr(db, "get_premium_entitlement"):
            override = await db.get_premium_entitlement(guild_id, feature)
            if override and override.get("limit_value") is not None:
                return int(override["limit_value"])
        return item.premium_limit
    return item.free_limit


async def describe(db, guild_id: int) -> dict[str, Any]:
    record = await active_record(db, guild_id)
    active = bool(record)
    features = []
    for item in FEATURES:
        features.append({
            "key": item.key,
            "name": item.name,
            "description": item.description,
            "enabled": await has_feature(db, guild_id, item.key),
            "free_limit": item.free_limit,
            "premium_limit": item.premium_limit,
        })
    return {
        "plan": str(record.get("plan_id") or PLAN_ID) if record else "free",
        "active": active,
        "started_at": record.get("started_at") if record else None,
        "expires_at": record.get("expires_at") if record else None,
        "features": features,
    }
