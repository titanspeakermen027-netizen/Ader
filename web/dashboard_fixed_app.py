"""Canonical Ader dashboard application.

Stable import target used by the Discord bot dashboard cog.
"""
from __future__ import annotations

from web.dashboard_cloud import create_app

__all__ = ("create_app",)
