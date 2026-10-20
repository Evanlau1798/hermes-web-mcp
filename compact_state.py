"""Interpret native completion timestamps without replacing their original values."""
import math
import re
import time
from datetime import datetime


def should_compact(data, now=None):
    tokens = data.get('contextUsage', {}).get('contextTokens')
    value = data.get('latestTurn', {}).get('completedAt')
    if not valid_timestamp(value) or isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 80000:
        return False
    seconds = (value / 1000 if value >= 1_000_000_000_000 else value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    return (data.get('compactState', {}).get('lastTurnIsCompaction') is False
            and (time.time() if now is None else now) - seconds > 1800)


def valid_timestamp(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            return False
        if not math.isfinite(number) or number <= 0:
            return False
        seconds = number / 1000 if number >= 1_000_000_000_000 else number
    elif isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)', value):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            seconds = parsed.timestamp() if parsed.utcoffset() is not None else 0
        except ValueError:
            return False
    else:
        return False
    return 0 < seconds <= time.time() + 5
