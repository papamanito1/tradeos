"""
Market Data Agent
Fetches and caches OHLCV, tickers, and order book data.
Publishes updates to Redis pub/sub for other agents.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.redis_client import redis_set, redis_get, redis_publish

logger = logging.getLogger(__name__)

TICKER_KEY = "market:ticker:{symbol}"
CANDLES_KEY = "market:candles:{symbol}:{timeframe}"
ORDERBOOK_KEY = "market:orderbook:{symbol}"


class MarketDataAgent:
    def __init__(self, exchange):
        self._exchange = exchange
        self._running = False
        self._subscriptions: dict[str, set[str]] = {}  # symbol -> set of timeframes
        self._tasks: list[asyncio.Task] = []

    def subscribe(self, symbol: str, timeframe: str = "1h") -> None:
        if symbol not in self._subscriptions:
            self._subscriptions[symbol] = set()
        self._subscriptions[symbol].add(timeframe)

    def unsubscribe(self, symbol: str, timeframe: Optional[str] = None) -> None:
        if symbol in self._subscriptions:
            if timeframe:
                self._subscriptions[symbol].discard(timeframe)
            else:
                del self._subscriptions[symbol]

    async def start(self) -> None:
        self._running = True
        logger.info("MarketDataAgent started")
        self._tasks.append(asyncio.create_task(self._ticker_loop()))
        self._tasks.append(asyncio.create_task(self._candle_loop()))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("MarketDataAgent stopped")

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        cached = await redis_get(TICKER_KEY.format(symbol=symbol))
        if cached:
            return cached
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
            data = {
                "symbol": ticker.symbol, "bid": ticker.bid, "ask": ticker.ask,
                "last": ticker.last, "volume": ticker.volume, "change_pct": ticker.change_pct,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            await redis_set(TICKER_KEY.format(symbol=symbol), data, ex=10)
            return data
        except Exception as e:
            logger.error(f"Failed to fetch ticker {symbol}: {e}")
            return None

    async def get_candles(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[dict]:
        cached = await redis_get(CANDLES_KEY.format(symbol=symbol, timeframe=timeframe))
        if cached:
            return cached
        return await self._fetch_and_cache_candles(symbol, timeframe, limit)

    async def _fetch_and_cache_candles(self, symbol: str, timeframe: str, limit: int) -> list[dict]:
        try:
            candles = await self._exchange.fetch_candles(symbol, timeframe, limit)
            data = [
                {
                    "timestamp": c.timestamp.isoformat(),
                    "open": c.open, "high": c.high, "low": c.low,
                    "close": c.close, "volume": c.volume,
                }
                for c in candles
            ]
            ttl_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
            await redis_set(CANDLES_KEY.format(symbol=symbol, timeframe=timeframe), data,
                            ex=ttl_map.get(timeframe, 3600))
            return data
        except Exception as e:
            logger.error(f"Failed to fetch candles {symbol}/{timeframe}: {e}")
            return []

    async def _ticker_loop(self) -> None:
        while self._running:
            for symbol in list(self._subscriptions.keys()):
                try:
                    ticker = await self._exchange.fetch_ticker(symbol)
                    data = {
                        "symbol": ticker.symbol, "bid": ticker.bid, "ask": ticker.ask,
                        "last": ticker.last, "volume": ticker.volume,
                        "change_pct": ticker.change_pct,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await redis_set(TICKER_KEY.format(symbol=symbol), data, ex=30)
                    await redis_publish("market:ticker", data)
                except Exception as e:
                    logger.warning(f"Ticker loop error {symbol}: {e}")
            await asyncio.sleep(5)

    async def _candle_loop(self) -> None:
        while self._running:
            for symbol, timeframes in list(self._subscriptions.items()):
                for tf in timeframes:
                    await self._fetch_and_cache_candles(symbol, tf, 200)
            await asyncio.sleep(60)
