"""Defuddle extract backend plus the ``web_research`` tool built on top of it."""
from __future__ import annotations

from .provider import DefuddleExtractProvider
from .tool import register_tool


def register(ctx) -> None:
    ctx.register_web_search_provider(DefuddleExtractProvider())
    register_tool(ctx)
