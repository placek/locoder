"""locoder routing: judge + weekly pacer + paid rungs + ledger, as four Hermes tools."""
from __future__ import annotations

from .tools import register as _register


def register(ctx) -> None:
    _register(ctx)
