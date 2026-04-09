"""
24/7 Living Agent Worker
────────────────────────
Runs as an asyncio background task on Railway.
Every 20 seconds:
  1. Fetches live BTC 1m + 15m candles from Binance REST API
  2. Fetches live order book snapshot
  3. Runs all 3 strategy engines
  4. Opens paper positions when signals qualify
  5. Closes positions when SL/TP is hit
  6. Stores everything in SQLite

Requires zero user interaction — keeps trading 24/7.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.agent247.models import (
    Agent247Config, Agent247Position, Agent247Trade, Agent247Stats,
)
from app.agent247.strategies import (
    Candle, OrderBook, OrderBookLevel,
    run_momentum, run_obi, run_orb,
    StrategyResult,
)

logger = logging.getLogger(__name__)

# ── Binance public REST endpoints ─────────────────────────────────────────────
BINANCE_BASE = "https://api.binance.com"


async def fetch_candles(client: httpx.AsyncClient, interval: str, limit: int) -> list[Candle]:
    try:
        r = await client.get(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        return [
            Candle(float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
            for row in r.json()
        ]
    except Exception as e:
        logger.warning(f"Candle fetch failed ({interval}): {e}")
        return []


async def fetch_orderbook(client: httpx.AsyncClient) -> OrderBook | None:
    try:
        r = await client.get(
            f"{BINANCE_BASE}/api/v3/depth",
            params={"symbol": "BTCUSDT", "limit": 10},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        bids = [OrderBookLevel(float(p), float(a)) for p, a in data["bids"]]
        asks = [OrderBookLevel(float(p), float(a)) for p, a in data["asks"]]
        return OrderBook(bids, asks)
    except Exception as e:
        logger.warning(f"Order book fetch failed: {e}")
        return None


# ── Log ring buffer (last 200 lines, shared across requests) ──────────────────
_log_buffer: deque[str] = deque(maxlen=200)

def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _log_buffer.appendleft(entry)
    logger.info(msg)


def get_log_lines() -> list[str]:
    return list(_log_buffer)


# ── Paper trading helpers ─────────────────────────────────────────────────────

async def _get_config() -> Agent247Config:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Agent247Config).where(Agent247Config.id == 1))
        cfg = result.scalar_one_or_none()
        if not cfg:
            cfg = Agent247Config(id=1)
            db.add(cfg)
            await db.commit()
            await db.refresh(cfg)
        return cfg


async def _get_stats() -> Agent247Stats:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Agent247Stats).where(Agent247Stats.id == 1))
        stats = result.scalar_one_or_none()
        if not stats:
            stats = Agent247Stats(id=1)
            db.add(stats)
            await db.commit()
            await db.refresh(stats)
        return stats


async def _open_position(sig, slot_result: StrategyResult, size_usdc: float) -> None:
    btc_size = size_usdc / sig.entry
    pos = Agent247Position(
        id            = str(uuid.uuid4()),
        strategy_key  = slot_result.strategy_key,
        strategy_name = slot_result.strategy_name,
        direction     = sig.direction,
        entry         = sig.entry,
        sl            = sig.sl,
        tp            = sig.tp,
        size_usdc     = size_usdc,
        confidence    = sig.confidence,
        reasoning     = sig.reasoning,
        opened_at     = datetime.utcnow(),
        current_price = sig.entry,
        unrealized_pnl= 0.0,
        unrealized_pct= 0.0,
        btc_size      = btc_size,
    )
    async with AsyncSessionLocal() as db:
        db.add(pos)
        await db.commit()
    _log(f"📄 [{slot_result.strategy_name}] OPEN {sig.direction.upper()} @ ${sig.entry:.0f} · ${ size_usdc} · SL ${sig.sl:.0f} · TP ${sig.tp:.0f}")


async def _close_position(pos: Agent247Position, exit_price: float, reason: str) -> None:
    diff    = (exit_price - pos.entry) if pos.direction == "long" else (pos.entry - exit_price)
    pnl_usd = diff * pos.btc_size
    pnl_pct = diff / pos.entry * 100

    trade = Agent247Trade(
        id            = pos.id,
        strategy_key  = pos.strategy_key,
        strategy_name = pos.strategy_name,
        direction     = pos.direction,
        entry         = pos.entry,
        sl            = pos.sl,
        tp            = pos.tp,
        size_usdc     = pos.size_usdc,
        confidence    = pos.confidence,
        reasoning     = pos.reasoning,
        is_paper      = True,
        exit_price    = round(exit_price, 2),
        exit_reason   = reason,
        pnl_usd       = round(pnl_usd, 2),
        pnl_pct       = round(pnl_pct, 4),
        status        = "confirmed",
        opened_at     = pos.opened_at,
        closed_at     = datetime.utcnow(),
    )
    async with AsyncSessionLocal() as db:
        # Delete position
        await db.execute(delete(Agent247Position).where(Agent247Position.id == pos.id))
        db.add(trade)
        # Update stats
        result = await db.execute(select(Agent247Stats).where(Agent247Stats.id == 1))
        stats = result.scalar_one_or_none() or Agent247Stats(id=1)
        stats.total_trades += 1
        if reason == "tp":   stats.wins   += 1
        elif reason == "sl": stats.losses += 1
        stats.win_rate    = round(stats.wins / stats.total_trades * 100, 1) if stats.total_trades else 0
        stats.total_pnl   = round((stats.total_pnl or 0) + pnl_usd, 2)
        stats.best_trade  = max(stats.best_trade  or 0, pnl_usd)
        stats.worst_trade = min(stats.worst_trade or 0, pnl_usd)
        db.add(stats)
        await db.commit()

    emoji = "✅" if reason == "tp" else "❌"
    _log(f"{emoji} [{pos.strategy_name}] {reason.upper()} @ ${exit_price:.0f} · P&L {'+' if pnl_usd >= 0 else ''}${pnl_usd:.2f}")


# ── Core scan ─────────────────────────────────────────────────────────────────

async def _scan(candles15m: list[Candle], candles1m: list[Candle], ob: OrderBook | None) -> None:
    cfg   = await _get_config()
    if not cfg.enabled:
        return

    # Run all 3 strategies
    results = [
        run_momentum(candles15m),
        run_obi(candles1m, ob),
        run_orb(candles1m),
    ]

    # Load open positions
    async with AsyncSessionLocal() as db:
        pos_rows = (await db.execute(select(Agent247Position))).scalars().all()

    open_by_key: dict[str, Agent247Position] = {p.strategy_key: p for p in pos_rows}
    live_price = candles1m[-1].close if candles1m else None

    # ── Check existing positions for SL/TP ───────────────────────────────────
    if live_price:
        for pos in pos_rows:
            hit_tp = (live_price >= pos.tp) if pos.direction == "long"  else (live_price <= pos.tp) if pos.tp else False
            hit_sl = (live_price <= pos.sl) if pos.direction == "long"  else (live_price >= pos.sl) if pos.sl else False
            if hit_tp or hit_sl:
                reason     = "tp" if hit_tp else "sl"
                exit_price = (pos.tp or live_price) if hit_tp else (pos.sl or live_price)
                await _close_position(pos, exit_price, reason)
                open_by_key.pop(pos.strategy_key, None)

    # ── Check for new signals ─────────────────────────────────────────────────
    for result in results:
        sig = result.signal
        if sig is None:
            _log(f"  [{result.strategy_name}] {result.met_count}/{result.total} conds · no signal · {result.bias}")
            continue

        _log(f"  [{result.strategy_name}] {result.met_count}/{result.total} conds · {sig.direction.upper()} · conf {sig.confidence*100:.0f}%")

        already_open = result.strategy_key in open_by_key
        conds_ok     = result.met_count >= cfg.min_conditions
        conf_ok      = sig.confidence   >= cfg.min_confidence

        if conds_ok and conf_ok and not already_open and cfg.auto_execute:
            await _open_position(sig, result, cfg.size_usdc)
        elif already_open:
            _log(f"    [{result.strategy_name}] position already open — skipping")


# ── Background worker loop ────────────────────────────────────────────────────

_worker_task: asyncio.Task | None = None


async def _worker_loop() -> None:
    _log("🟢 Living Agent 24/7 started — scanning every 20s")
    scan_count = 0

    async with httpx.AsyncClient() as client:
        # Seed initial candle history
        candles15m = await fetch_candles(client, "15m", 120)
        candles1m  = await fetch_candles(client, "1m",  300)
        ob         = await fetch_orderbook(client)
        _log(f"Seeded {len(candles15m)} × 15m + {len(candles1m)} × 1m candles")

        while True:
            try:
                await asyncio.sleep(20)
                scan_count += 1

                # Refresh market data
                new15 = await fetch_candles(client, "15m", 3)
                new1  = await fetch_candles(client, "1m",  5)
                ob    = await fetch_orderbook(client)

                if new15:
                    # Merge new 15m candles
                    if candles15m and new15[-1].close == candles15m[-1].close:
                        candles15m[-1] = new15[-1]
                    else:
                        candles15m = (candles15m + new15)[-120:]

                if new1:
                    if candles1m and new1[-1].close == candles1m[-1].close:
                        candles1m[-1] = new1[-1]
                    else:
                        candles1m = (candles1m + new1)[-300:]

                price = candles1m[-1].close if candles1m else 0
                _log(f"── Scan #{scan_count} · BTC ${price:,.0f} · {len(candles15m)} 15m · {len(candles1m)} 1m bars")
                await _scan(candles15m, candles1m, ob)

            except asyncio.CancelledError:
                _log("🔴 Living Agent stopped")
                return
            except Exception as e:
                _log(f"⚠ Worker error: {e}")
                await asyncio.sleep(10)


def start_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop())
    logger.info("Agent247 worker started")


def stop_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None


def worker_running() -> bool:
    return _worker_task is not None and not _worker_task.done()
