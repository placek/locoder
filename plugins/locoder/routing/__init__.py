"""locoder routing: judge + Claude-first policy + paid rungs + ledger, as Hermes tools."""
from __future__ import annotations

from .tools import register as _register


def register(ctx) -> None:
    _register(ctx)
