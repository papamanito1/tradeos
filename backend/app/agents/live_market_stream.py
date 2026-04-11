"""
Live Market Stream Agent
========================
Fetches real-time market data from BingX perpetual swap API (primary)
with multi-source fallback for resilience.

BingX REST endpoints (public, no API key):
  /openApi/swap/v2/quote/ticker  → 24h ticker + bid/ask
  /openApi/swap/v3/quote/klines  → OHLCV candles
  /openApi/swap/v2/quote/depth   → Order book levels

Data flow:
  BingX REST → LiveMarketStreamAgent → price_cache (in-memory)
                                     → redis_publish (→ TradeOS WS → frontend)

The price_cache is a simple module-level dict so the paper trading engine
can read the latest price instantly without any I/O.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from app.core.redis_client import redis_set, redis_publish

logger = logging.getLogger(__name__)

# ── Module-level live price cache ─────────────────────────────────────────────
LIVE_PRICES: dict[str, dict] = {}
LIVE_CANDLES: dict[str, list[dict]] = {}
LIVE_ORDERBOOK: dict[str, dict] = {}

BINGX_BASE = "https://open-api.bingx.com"

DEFAULT_SYMBOLS = ["BTC/USDT"]
TIMEFRAMES = ["1m"]


def _ccxt_to_bingx(symbol: str) -> str:
    """Convert 'BTC/USDT' → 'BTC-USDT'"""
    return symbol.replace("/", "-")


def _ccxt_to_binance(symbol: str) -> str:
    """Convert 'BTC/USDT' → 'btcusdt' (for Binance fallback)"""
    return symbol.replace("/", "").lower()


class LiveMarketStreamAgent:
    """
    Polls BingX perpetual swap REST API for market data.
    Falls back to Bybit / CoinGecko if BingX is unreachable.
    """

    def __init__(self, symbols: list[str] = None, use_testnet: bool = False):
        self._symbols = symbols or DEFAULT_SYMBOLS
        self._use_testnet = use_testnet
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def get_price(self, symbol: str) -> Optional[float]:
        data = LIVE_PRICES.get(symbol)
        return data["last"] if data else None

    def get_ticker(self, symbol: str) -> Optional[dict]:
        return LIVE_PRICES.get(symbol)

    def get_candles(self, symbol: str, timeframe: str = "1m") -> list[dict]:
        return LIVE_CANDLES.get(f"{symbol}:{timeframe}", [])

    def get_orderbook(self, symbol: str) -> Optional[dict]:
        return LIVE_ORDERBOOK.get(symbol)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        await self._seed_historical()
        self._tasks.append(asyncio.create_task(self._rest_price_loop()))
        self._tasks.append(asyncio.create_task(self._rest_candle_loop()))
        self._tasks.append(asyncio.create_task(self._rest_depth_loop()))
        logger.info(f"LiveMarketStreamAgent started for {self._symbols} (BingX primary)")

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("LiveMarketStreamAgent stopped")

    # ── BingX REST: Ticker ─────────────────────────────────────────────────────

    @staticmethod
    async def _fetch_bingx_ticker(sym: str) -> Optional[dict]:
        """Fetch full ticker from BingX perpetual swap."""
        import httpx
        bingx_sym = _ccxt_to_bingx(sym)
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    f"{BINGX_BASE}/openApi/swap/v2/quote/ticker",
                    params={"symbol": bingx_sym},
                )
                if r.status_code == 200:
                    j = r.json()
                    if j.get("code") == 0 and j.get("data"):
                        d = j["data"]
                        return {
                            "symbol": sym,
                            "last":         float(d.get("lastPrice", 0)),
                            "bid":          float(d.get("bidPrice", 0)),
                            "ask":          float(d.get("askPrice", 0)),
                            "volume":       float(d.get("volume", 0)),
                            "quote_volume": float(d.get("quoteVolume", 0)),
                            "change_pct":   float(d.get("priceChangePercent", 0)),
                            "high_24h":     float(d.get("highPrice", 0)),
                            "low_24h":      float(d.get("lowPrice", 0)),
                            "open_24h":     float(d.get("openPrice", 0)),
                            "updated_at":   datetime.now(timezone.utc).isoformat(),
                            "updated_ms":   int(time.time() * 1000),
                        }
        except Exception as e:
            logger.warning(f"[MarketStream] BingX ticker failed: {e}")
        return None

    # ── BingX REST: Price (multi-source fallback) ──────────────────────────────

    @staticmethod
    async def _fetch_price(sym: str) -> float:
        """
        Fetch BTC price. BingX first, then fallback chain.
        """
        import httpx
        bingx_sym = _ccxt_to_bingx(sym)
        sources = [
            ("BingX", f"{BINGX_BASE}/openApi/swap/v2/quote/ticker?symbol={bingx_sym}",
             lambda d: float(d["data"]["lastPrice"])),
            ("CoinGecko", "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
             lambda d: float(d["bitcoin"]["usd"])),
            ("blockchain.info", "https://blockchain.info/ticker",
             lambda d: float(d["USD"]["last"])),
            ("Bybit", f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={_ccxt_to_binance(sym).upper()}",
             lambda d: float(d["result"]["list"][0]["lastPrice"])),
            ("Kraken", "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
             lambda d: float(d["result"]["XXBTZUSD"]["c"][0])),
        ]
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            for name, url, parse in sources:
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        price = parse(r.json())
                        if price > 0:
                            logger.info(f"[MarketStream] BTC price ${price:,.0f} via {name}")
                            return price
                    else:
                        logger.warning(f"[MarketStream] {name} HTTP {r.status_code}")
                except Exception as e:
                    logger.warning(f"[MarketStream] {name} failed: {type(e).__name__}: {e}")
        logger.error(f"[MarketStream] ALL price sources failed for {sym}")
        return 0.0

    # ── BingX REST: Candles ────────────────────────────────────────────────────

    @staticmethod
    async def _fetch_candles(sym: str, tf: str, limit: int) -> list:
        """Fetch OHLCV candles. BingX first, Bybit fallback."""
        import httpx
        bingx_sym = _ccxt_to_bingx(sym)

        async def _parse_bingx() -> list:
            url = f"{BINGX_BASE}/openApi/swap/v3/quote/klines"
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(url, params={
                    "symbol": bingx_sym, "interval": tf, "limit": str(limit),
                })
                j = r.json()
                if j.get("code") != 0 or not j.get("data"):
                    return []
                rows = sorted(j["data"], key=lambda x: x["time"])
                return [{
                    "timestamp": datetime.fromtimestamp(
                        int(row["time"]) / 1000, tz=timezone.utc
                    ).isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "is_closed": True,
                } for row in rows]

        async def _parse_bybit() -> list:
            bybit_tf = {"1m": "1", "5m": "5", "15m": "15", "30m": "30",
                         "1h": "60", "4h": "240", "1d": "D"}.get(tf, "1")
            b = _ccxt_to_binance(sym).upper()
            url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={b}&interval={bybit_tf}&limit={limit}"
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(url)
                rows = list(reversed(r.json()["result"]["list"]))
                return [{
                    "timestamp": datetime.fromtimestamp(
                        int(row[0]) / 1000, tz=timezone.utc
                    ).isoformat(),
                    "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4]),
                    "volume": float(row[5]), "is_closed": True,
                } for row in rows]

        for name, fn in [("BingX", _parse_bingx), ("Bybit", _parse_bybit)]:
            try:
                candles = await fn()
                if candles:
                    logger.info(f"[MarketStream] {len(candles)} {tf} candles via {name}")
                    return candles
            except Exception as e:
                logger.warning(f"[MarketStream] Candles {name} {tf} failed: {type(e).__name__}: {e}")

        logger.error(f"[MarketStream] ALL candle sources failed for {sym} {tf}")
        return []

    # ── BingX REST: Depth ──────────────────────────────────────────────────────

    @staticmethod
    async def _fetch_depth(sym: str, depth_limit: int = 20) -> Optional[dict]:
        """Fetch order book from BingX perpetual swap."""
        import httpx
        bingx_sym = _ccxt_to_bingx(sym)
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    f"{BINGX_BASE}/openApi/swap/v2/quote/depth",
                    params={"symbol": bingx_sym, "limit": str(depth_limit)},
                )
                if r.status_code == 200:
                    j = r.json()
                    if j.get("code") == 0 and j.get("data"):
                        d = j["data"]
                        return {
                            "symbol": sym,
                            "bids": [{"price": float(b[0]), "amount": float(b[1])}
                                     for b in (d.get("bids") or [])[:depth_limit]],
                            "asks": [{"price": float(a[0]), "amount": float(a[1])}
                                     for a in (d.get("asks") or [])[:depth_limit]],
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
        except Exception as e:
            logger.warning(f"[MarketStream] BingX depth failed: {e}")
        return None

    # ── Seed historical data on startup ────────────────────────────────────────

    async def _seed_historical(self) -> None:
        logger.info("[MarketStream] Seeding historical data from BingX...")
        for sym in self._symbols:
            # Seed ticker / price
            ticker = await self._fetch_bingx_ticker(sym)
            if ticker and ticker["last"] > 0:
                LIVE_PRICES[sym] = ticker
                self._connected = True
                logger.info(f"[MarketStream] {sym} seeded @ ${ticker['last']:,.0f} via BingX")
            else:
                price = await self._fetch_price(sym)
                if price > 0:
                    LIVE_PRICES[sym] = {
                        "symbol": sym, "last": price, "bid": price, "ask": price,
                        "volume": 0, "quote_volume": 0, "change_pct": 0,
                        "high_24h": price, "low_24h": price, "open_24h": price,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_ms": int(time.time() * 1000),
                    }
                    self._connected = True

            # Seed candles — 1m, 15m for strategies; 1h, 4h for macro trend
            for tf, limit in [("1m", 350), ("15m", 150), ("1h", 100), ("4h", 60)]:
                candles = await self._fetch_candles(sym, tf, limit)
                if candles:
                    key = f"{sym}:{tf}"
                    existing = LIVE_CANDLES.get(key, [])
                    existing_ts = {c["timestamp"] for c in existing}
                    merged = [c for c in candles if c["timestamp"] not in existing_ts] + existing
                    LIVE_CANDLES[key] = sorted(merged, key=lambda x: x["timestamp"])[-limit:]
                    logger.info(f"[MarketStream] {sym}:{tf} → {len(LIVE_CANDLES[key])} candles")

            # Seed order book
            ob = await self._fetch_depth(sym)
            if ob:
                LIVE_ORDERBOOK[sym] = ob
                logger.info(f"[MarketStream] {sym} order book seeded ({len(ob['bids'])} bids)")

    # ── REST polling loops ─────────────────────────────────────────────────────

    async def _rest_price_loop(self) -> None:
        """Poll BingX ticker every 5s for price + 24h stats."""
        logger.info("[MarketStream] BingX ticker loop started (5s interval)")
        while self._running:
            for sym in self._symbols:
                ticker = await self._fetch_bingx_ticker(sym)
                if ticker and ticker["last"] > 0:
                    LIVE_PRICES[sym] = ticker
                    self._connected = True
                    await redis_set(f"market:ticker:{sym}", ticker, ex=10)
                    await redis_publish("market:ticker", ticker)
                else:
                    price = await self._fetch_price(sym)
                    if price > 0:
                        existing = LIVE_PRICES.get(sym, {})
                        LIVE_PRICES[sym] = {
                            **existing, "symbol": sym, "last": price,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "updated_ms": int(time.time() * 1000),
                        }
            await asyncio.sleep(5)

    async def _rest_candle_loop(self) -> None:
        """Refresh latest candles from BingX every 5s (1m/15m) and every 5 min (1h/4h)."""
        logger.info("[MarketStream] BingX candle refresh loop started (5s interval)")
        _slow_refresh_counter = 0
        while self._running:
            _slow_refresh_counter += 1
            # 1m and 15m refresh every 5s (fast); 1h and 4h refresh every 5 min (60 ticks)
            tfs_fast = ["1m", "15m"]
            tfs_slow = ["1h", "4h"] if _slow_refresh_counter % 60 == 0 else []
            for sym in self._symbols:
                for tf in tfs_fast + tfs_slow:
                    try:
                        new_candles = await self._fetch_candles(sym, tf, 3)
                        if new_candles:
                            key = f"{sym}:{tf}"
                            candles = LIVE_CANDLES.get(key, [])
                            for c in new_candles:
                                if candles and candles[-1]["timestamp"] == c["timestamp"]:
                                    candles[-1] = c
                                elif not candles or c["timestamp"] > candles[-1]["timestamp"]:
                                    candles.append(c)
                            limit = {"1m": 500, "15m": 200, "1h": 100, "4h": 60}.get(tf, 200)
                            LIVE_CANDLES[key] = candles[-limit:]
                            await redis_publish("market:kline", {
                                "symbol": sym, "timeframe": tf, "candle": new_candles[-1],
                            })
                    except Exception as e:
                        logger.warning(f"[MarketStream] Candle refresh {tf} error: {e}")
            await asyncio.sleep(5)

    async def _rest_depth_loop(self) -> None:
        """Refresh order book from BingX every 3s."""
        logger.info("[MarketStream] BingX depth loop started (3s interval)")
        while self._running:
            for sym in self._symbols:
                ob = await self._fetch_depth(sym)
                if ob:
                    LIVE_ORDERBOOK[sym] = ob
                    await redis_set(f"market:orderbook:{sym}", ob, ex=5)
                    await redis_publish("market:orderbook", ob)
            await asyncio.sleep(3)


# ── Module-level singleton ─────────────────────────────────────────────────────
_agent: Optional[LiveMarketStreamAgent] = None


def get_live_stream_agent() -> Optional[LiveMarketStreamAgent]:
    return _agent


def create_live_stream_agent(symbols: list[str] = None) -> LiveMarketStreamAgent:
    global _agent
    _agent = LiveMarketStreamAgent(symbols=symbols or DEFAULT_SYMBOLS)
    return _agent
