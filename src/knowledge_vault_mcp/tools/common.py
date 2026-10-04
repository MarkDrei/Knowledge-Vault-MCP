"""Helpers shared by the tool modules."""

import datetime as dt
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError

from knowledge_vault_mcp.config import Settings


def tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def iso_time(settings: Settings, ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, tz(settings)).isoformat(timespec="seconds")


def parse_time(settings: Settings, value: str | None, name: str) -> int | None:
    """Parse an ISO date or datetime; dates and naive times are in the configured timezone."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except ValueError as e:
        raise ToolError(f"{name}: expected an ISO date like 2026-10-01 or 2026-10-01T12:00") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz(settings))
    return int(parsed.timestamp())
