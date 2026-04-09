"""
Signal Agent
============
Runs active strategies against live market data every minute,
passes signals through the execution agent, and stores them
in the signal cache for chart overlay.

Data source priority:
  1. LIVE_CANDLES from LiveMarketStreamAgent (real-time 1m data, zero delay)
  2. Bybit REST API for historical bars / non-1m timeframes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from app.strategies import STRATEGY_REGISTRY, Signal, SignalDirection
from app.exchange.base import Candle
from app.core.redis_client import redis_publish
from app.core.signal_cache import store_signal

logger = logging.getLogger(__name__)

# Bybit interval map
_BYBIT_TF: dict[str, str] = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
}


async def _fetch_candles_rest(symbol: str, timeframe: str, limit: int = 250) -> list[Candle]:
    """Fetch historical OHLCV from Bybit (no API key, no geo-blocks)."""
    bsym = symbol.replace("/", "")
    iv = _BYBIT_TF.get(timeframe, "60")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.bybit.com/v5/market/kline",
                params={"category": "spot", "symbol": bsym, "interval": iv, "limit": limit},
            )
            resp.raise_for_status()
            rows = resp.json().get("result", {}).get("list", [])
            rows = list(reversed(rows))  # Bybit returns newest first
            return [
                Candle(
                    timestamp=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                    open=float(r[1]), high=float(r[2]), low=float(r[3]),
                    close=float(r[4]), volume=float(r[5]),
                )
                for r in rows
            ]
    except Exception as e:
        logger.warning(f"REST candle fetch failed {symbol}/{timeframe}: {e}")
        return []


def _live_candles_to_candle_list(symbol: str, timeframe: str) -> list[Candle]:
    """Pull from in-memory live stream cache (1m bars only)."""
    from app.agents.live_market_stream import LIVE_CANDLES
    key = f"{symbol}:{timeframe}"
    raw = LIVE_CANDLES.get(key, [])
    out = []
    for r in raw:
        try:
            out.append(Candle(
                timestamp=datetime.fromisoformat(r["timestamp"]),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]),  close=float(r["close"]),
                volume=float(r["volume"]),
            ))
        except Exception:
            pass
    return out


class SignalAgent:
    def __init__(self, execution_agent=None):
        self._execution_agent = execution_agent
        self._running = False
        self._active_strategies: list[dict] = []
        self._tasks: list[asyncio.Task] = []
        self._strategy_names: dict[str, str] = {}  # type → display name

    def set_execution_agent(self, agent) -> None:
        self._execution_agent = agent

    def update_strategies(self, strategies: list[dict]) -> None:
        self._active_strategies = [s for s in strategies if s.get("is_enabled")]

    async def start(self) -> None:
        self._running = True
        # Build display name map
        for k, cls in STRATEGY_REGISTRY.items():
            self._strategy_names[k] = getattr(cls, "name", k)
        self._tasks.append(asyncio.create_task(self._signal_loop()))
        logger.info("SignalAgent started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _signal_loop(self) -> None:
        """Align to the top of each minute then run every 60 s."""
        # Wait until the next whole minute so signals align with closed candles
        now = datetime.now(timezone.utc)
        wait = 60 - now.second - now.microsecond / 1e6
        await asyncio.sleep(max(wait, 2))

        while self._running:
            await self._run_all_strategies()
            await asyncio.sleep(60)

    async def _run_all_strategies(self) -> None:
        # Reload strategies from DB on each cycle
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.strategy import Strategy
            from sqlalchemy import select
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Strategy).where(Strategy.is_enabled == True, Strategy.mode != "off")
                )
                db_strats = result.scalars().all()
                self._active_strategies = [
                    {
                        "id": s.id,
                        "strategy_type": s.strategy_type,
                        "symbols": s.symbols,
                        "timeframe": s.timeframe,
                        "parameters": s.parameters or {},
                        "mode": s.mode,
                        "capital_allocation": s.capital_allocation or 500.0,
                        "is_enabled": s.is_enabled,
                    }
                    for s in db_strats
                ]
        except Exception as e:
            logger.warning(f"Could not reload strategies from DB: {e}")

        for strategy_cfg in self._active_strategies:
            try:
                await self._run_strategy(strategy_cfg)
            except Exception as e:
                logger.error(f"Strategy loop error [{strategy_cfg.get('strategy_type')}]: {e}")

    async def _run_strategy(self, cfg: dict) -> None:
        strategy_type = cfg.get("strategy_type", "")
        cls = STRATEGY_REGISTRY.get(strategy_type)
        if not cls:
            return

        strategy = cls(parameters=cfg.get("parameters", {}))
        strategy_name = self._strategy_names.get(strategy_type, strategy_type)
        timeframe = cfg.get("timeframe", "1h")

        for symbol in cfg.get("symbols", []):
            candles = await self._get_candles(symbol, timeframe)
            if len(candles) < 30:
                logger.debug(f"Not enough candles for {strategy_type}/{symbol}: {len(candles)}")
                continue

            try:
                signal = strategy.generate_signal(candles, symbol, timeframe)
            except Exception as e:
                logger.error(f"generate_signal error [{strategy_type}/{symbol}]: {e}")
                continue

            if signal.direction in (SignalDirection.none,):
                continue

            logger.info(
                f"SIGNAL [{strategy_name}] {signal.direction.upper()} {symbol} "
                f"@ {signal.suggested_entry:.2f} | conf={signal.confidence:.2f}"
            )

            # Store in cache for chart overlay
            store_signal(
                symbol=symbol,
                direction=signal.direction,
                entry=signal.suggested_entry or 0,
                sl=signal.suggested_sl,
                tp=signal.suggested_tp,
                strategy_id=cfg["id"],
                strategy_name=strategy_name,
                confidence=signal.confidence,
                reasoning=signal.reasoning,
                timeframe=timeframe,
            )

            # Publish to WebSocket (frontend sees it live)
            await redis_publish("signal:new", {
                "strategy_id": cfg["id"],
                "strategy_name": strategy_name,
                "direction": signal.direction,
                "symbol": symbol,
                "confidence": signal.confidence,
                "entry": signal.suggested_entry,
                "sl": signal.suggested_sl,
                "tp": signal.suggested_tp,
                "reasoning": signal.reasoning,
                "timeframe": timeframe,
            })

            # Execute paper trade
            if self._execution_agent and cfg.get("mode") == "paper":
                await self._execute(signal, cfg, strategy_name)

    async def _execute(self, signal: Signal, cfg: dict, strategy_name: str) -> None:
        """Pass signal through risk checks and place paper order."""
        try:
            # Get current paper state for risk checks
            from app.exchange.paper_trading import PaperTradingEngine, PAPER_BALANCE_KEY
            from app.core.redis_client import redis_get

            paper = PaperTradingEngine()
            state = await paper._get_state()
            balance_usd = state.get("balance_usd", 10000.0)
            positions = state.get("positions", {})
            open_trade_count = len(positions)
            symbol_exposure = 0.0
            if signal.symbol in positions:
                pos = positions[signal.symbol]
                symbol_exposure = abs(pos.get("size", 0)) * (pos.get("entry_price", 0))

            position_size_usd = min(
                cfg.get("capital_allocation", 500.0),
                balance_usd * 0.20,   # never more than 20% per trade
            )

            result = await self._execution_agent.handle_signal(
                signal=signal,
                strategy_id=cfg["id"],
                balance_usd=balance_usd,
                open_trade_count=open_trade_count,
                symbol_exposure_usd=symbol_exposure,
                position_size_usd=position_size_usd,
            )
            if result:
                logger.info(
                    f"PAPER TRADE EXECUTED [{strategy_name}] {result['side'].upper()} "
                    f"{result['amount']:.6f} {signal.symbol} @ {result['fill_price']:.2f}"
                )
        except Exception as e:
            logger.error(f"Execution error [{strategy_name}/{signal.symbol}]: {e}")

    async def _get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> list[Candle]:
        """Use live 1m cache when available, else fetch from Bybit REST."""
        if timeframe == "1m":
            live = _live_candles_to_candle_list(symbol, "1m")
            if len(live) >= 30:
                # Pad with historical data if the stream just started
                if len(live) < limit:
                    hist = await _fetch_candles_rest(symbol, "1m", limit)
                    # Merge: historical first, then live (deduplicate by timestamp)
                    seen = {c.timestamp for c in live}
                    merged = [c for c in hist if c.timestamp not in seen] + live
                    merged.sort(key=lambda c: c.timestamp)
                    return merged[-limit:]
                return live[-limit:]

        # Non-1m or live cache not ready → use REST
        return await _fetch_candles_rest(symbol, timeframe, limit)
