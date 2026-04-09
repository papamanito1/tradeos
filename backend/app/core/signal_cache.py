"""
Signal Cache — in-memory store for recent strategy signals.
Used to render signal markers on the chart in the frontend.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
import threading

_lock = threading.Lock()
_cache: dict[str, list[dict]] = {}   # symbol → [signal_dict, ...]
MAX_PER_SYMBOL = 200


def store_signal(
    symbol: str,
    direction: str,
    entry: float,
    sl: Optional[float],
    tp: Optional[float],
    strategy_id: int,
    strategy_name: str,
    confidence: float,
    reasoning: str,
    timeframe: str,
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "direction": direction,     # "long" | "short"
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "confidence": round(confidence, 3),
        "reasoning": reasoning,
        "timeframe": timeframe,
        "symbol": symbol,
    }
    with _lock:
        bucket = _cache.setdefault(symbol, [])
        bucket.append(record)
        if len(bucket) > MAX_PER_SYMBOL:
            _cache[symbol] = bucket[-MAX_PER_SYMBOL:]


def get_signals(symbol: str, limit: int = 50) -> list[dict]:
    with _lock:
        return list(_cache.get(symbol, [])[-limit:])


def get_all() -> dict[str, list[dict]]:
    with _lock:
        return {k: list(v) for k, v in _cache.items()}
