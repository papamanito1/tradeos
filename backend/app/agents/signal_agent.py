"""
Signal Agent
Periodically runs enabled strategies against fresh market data
and publishes signals to other agents.
"""
import asyncio
import logging
from typing import Callable, Awaitable

from app.strategies import STRATEGY_REGISTRY, Signal, SignalDirection
from app.agents.market_data_agent import MarketDataAgent
from app.exchange.base import Candle
from app.core.redis_client import redis_publish

logger = logging.getLogger(__name__)

SignalCallback = Callable[[Signal, int], Awaitable[None]]


class SignalAgent:
    def __init__(self, market_data: MarketDataAgent):
        self._market_data = market_data
        self._running = False
        self._callbacks: list[SignalCallback] = []
        self._active_strategies: list[dict] = []  # [{id, type, symbols, timeframe, parameters}]
        self._tasks: list[asyncio.Task] = []

    def register_callback(self, cb: SignalCallback) -> None:
        self._callbacks.append(cb)

    def update_strategies(self, strategies: list[dict]) -> None:
        self._active_strategies = [s for s in strategies if s.get("is_enabled")]

    async def start(self) -> None:
        self._running = True
        self._tasks.append(asyncio.create_task(self._signal_loop()))
        logger.info("SignalAgent started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def _signal_loop(self) -> None:
        while self._running:
            for strategy_cfg in self._active_strategies:
                if strategy_cfg.get("mode", "off") == "off":
                    continue
                strategy_type = strategy_cfg.get("strategy_type")
                strategy_class = STRATEGY_REGISTRY.get(strategy_type)
                if not strategy_class:
                    logger.warning(f"Unknown strategy type: {strategy_type}")
                    continue

                strategy = strategy_class(parameters=strategy_cfg.get("parameters", {}))

                for symbol in strategy_cfg.get("symbols", []):
                    timeframe = strategy_cfg.get("timeframe", "1h")
                    candle_data = await self._market_data.get_candles(symbol, timeframe, 200)
                    if not candle_data:
                        continue

                    from datetime import datetime, timezone
                    candles = [
                        Candle(
                            timestamp=datetime.fromisoformat(c["timestamp"]),
                            open=c["open"], high=c["high"], low=c["low"],
                            close=c["close"], volume=c["volume"],
                        )
                        for c in candle_data
                    ]

                    try:
                        signal = strategy.generate_signal(candles, symbol, timeframe)
                        if signal.direction != SignalDirection.none:
                            await redis_publish("signal:new", {
                                "strategy_id": strategy_cfg["id"],
                                "direction": signal.direction,
                                "symbol": symbol,
                                "confidence": signal.confidence,
                                "reasoning": signal.reasoning,
                                "entry": signal.suggested_entry,
                                "sl": signal.suggested_sl,
                                "tp": signal.suggested_tp,
                            })
                            for cb in self._callbacks:
                                await cb(signal, strategy_cfg["id"])
                    except Exception as e:
                        logger.error(f"Signal error [{strategy_type}/{symbol}]: {e}")

            await asyncio.sleep(30)  # run every 30 seconds
