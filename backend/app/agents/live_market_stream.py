"""
Live Market Stream Agent
========================
Opens a persistent WebSocket connection to Binance's free public streams.
No API keys required — all data is publicly available.

Streams subscribed:
  {symbol}@ticker         → Best bid/ask + last price + 24h stats (real-time)
  {symbol}@kline_1m       → 1-minute OHLCV candle (updates every ~1s)
  {symbol}@depth20@100ms  → Top-20 order book levels (100ms refresh)

Binance stream docs: https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams

Data flow:
  Binance WS → LiveMarketStreamAgent → price_cache (in-memory)
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
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from app.core.redis_client import redis_set, redis_publish

logger = logging.getLogger(__name__)

# ── Module-level live price cache ─────────────────────────────────────────────
# Key: "BTC/USDT" → {last, bid, ask, volume, change_pct, updated_at}
# Written by this agent, read by PaperTradingEngine and any API handler.
LIVE_PRICES: dict[str, dict] = {}

# Key: "BTC/USDT:1m" → list of {timestamp, open, high, low, close, volume}
LIVE_CANDLES: dict[str, list[dict]] = {}

# Key: "BTC/USDT" → {bids: [...], asks: [...], timestamp}
LIVE_ORDERBOOK: dict[str, dict] = {}

BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream"
BINANCE_WS_TESTNET = "wss://testnet.binance.vision/stream"

# Default watchlist (no API key required for any of these)
DEFAULT_SYMBOLS = ["BTC/USDT"]
TIMEFRAMES = ["1m"]


def _ccxt_to_binance(symbol: str) -> str:
    """Convert 'BTC/USDT' → 'btcusdt'"""
    return symbol.replace("/", "").lower()


def _binance_to_ccxt(symbol: str) -> str:
    """Convert 'BTCUSDT' → 'BTC/USDT' (assumes USDT quote)"""
    sym = symbol.upper()
    for quote in ["USDT", "BTC", "ETH", "BNB", "BUSD"]:
        if sym.endswith(quote):
            base = sym[: -len(quote)]
            return f"{base}/{quote}"
    return sym


class LiveMarketStreamAgent:
    """
    Maintains a single multiplexed WebSocket connection to Binance.
    Reconnects automatically on disconnect with exponential backoff.
    Falls back to REST polling if WebSocket is unavailable.
    """

    def __init__(self, symbols: list[str] = None, use_testnet: bool = False):
        self._symbols = symbols or DEFAULT_SYMBOLS
        self._use_testnet = use_testnet
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
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
        # Seed historical candles + current price immediately via REST
        await self._seed_historical()
        # Always run a REST price-refresh loop (every 5 s) as the primary price feed.
        # WebSocket is layered on top for sub-second updates when available.
        self._tasks.append(asyncio.create_task(self._rest_price_loop()))
        if HAS_WEBSOCKETS:
            self._tasks.append(asyncio.create_task(self._ws_loop()))
        else:
            logger.warning("websockets library not available — using REST fallback for candles too")
            self._tasks.append(asyncio.create_task(self._rest_fallback_loop()))
        logger.info(f"LiveMarketStreamAgent started for {self._symbols}")

    # ── Multi-source REST helpers (httpx — more reliable than aiohttp) ──────────

    @staticmethod
    async def _fetch_price(sym: str) -> float:
        """
        Try multiple public APIs for BTC price.
        Order: CoinGecko → blockchain.info → Kraken → Bybit → Binance US → Binance.
        Every failure is logged at WARNING so Railway logs show what's happening.
        """
        import httpx
        sources = [
            # 1. CoinGecko — very permissive, no geo-blocks
            ("CoinGecko", "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
             lambda d: float(d["bitcoin"]["usd"])),
            # 2. blockchain.info — old reliable, never blocked
            ("blockchain.info", "https://blockchain.info/ticker",
             lambda d: float(d["USD"]["last"])),
            # 3. Kraken — EU exchange, no US IP blocks for public API
            ("Kraken", "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
             lambda d: float(d["result"]["XXBTZUSD"]["c"][0])),
            # 4. Bybit spot
            ("Bybit", f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={_ccxt_to_binance(sym).upper()}",
             lambda d: float(d["result"]["list"][0]["lastPrice"])),
            # 5. Binance US (allowed on US IPs)
            ("BinanceUS", f"https://api.binance.us/api/v3/ticker/price?symbol={_ccxt_to_binance(sym).upper()}",
             lambda d: float(d["price"])),
            # 6. Binance global (geo-blocked on US infra — last resort)
            ("Binance", f"https://api.binance.com/api/v3/ticker/price?symbol={_ccxt_to_binance(sym).upper()}",
             lambda d: float(d["price"])),
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
                        logger.warning(f"[MarketStream] {name} returned price=0")
                    else:
                        logger.warning(f"[MarketStream] {name} HTTP {r.status_code}")
                except Exception as e:
                    logger.warning(f"[MarketStream] {name} failed: {type(e).__name__}: {e}")
        logger.error(f"[MarketStream] ALL price sources failed for {sym} — agent will skip scans!")
        return 0.0

    @staticmethod
    async def _fetch_candles(sym: str, tf: str, limit: int) -> list:
        """Try Bybit → Kraken/OKX → Binance for historical OHLCV candles."""
        import httpx
        b = _ccxt_to_binance(sym).upper()

        async def _parse_bybit() -> list:
            bybit_tf = {"1m": "1", "15m": "15"}.get(tf, "1")
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={b}&interval={bybit_tf}&limit={limit}"
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(url)
                rows = list(reversed(r.json()["result"]["list"]))
                return [{"timestamp": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
                         "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                         "close": float(row[4]), "volume": float(row[5]), "is_closed": True} for row in rows]

        async def _parse_binance_us() -> list:
            url = f"https://api.binance.us/api/v3/klines?symbol={b}&interval={tf}&limit={limit}"
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(url)
                rows = r.json()
                return [{"timestamp": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
                         "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                         "close": float(row[4]), "volume": float(row[5]), "is_closed": True} for row in rows]

        async def _parse_binance() -> list:
            url = f"https://api.binance.com/api/v3/klines?symbol={b}&interval={tf}&limit={limit}"
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(url)
                rows = r.json()
                return [{"timestamp": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
                         "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                         "close": float(row[4]), "volume": float(row[5]), "is_closed": True} for row in rows]

        for name, fn in [("Bybit", _parse_bybit), ("BinanceUS", _parse_binance_us), ("Binance", _parse_binance)]:
            try:
                candles = await fn()
                if candles:
                    logger.info(f"[MarketStream] {len(candles)} {tf} candles via {name}")
                    return candles
            except Exception as e:
                logger.warning(f"[MarketStream] Candles {name} {tf} failed: {type(e).__name__}: {e}")

        logger.error(f"[MarketStream] ALL candle sources failed for {sym} {tf}")
        return []

    async def _seed_historical(self) -> None:
        """Fetch price + candles on startup using multi-source fallback."""
        logger.info("[MarketStream] Seeding historical data...")
        for sym in self._symbols:
            price = await self._fetch_price(sym)
            if price > 0:
                LIVE_PRICES[sym] = {
                    "symbol": sym, "last": price, "bid": price, "ask": price,
                    "volume": 0, "quote_volume": 0, "change_pct": 0,
                    "high_24h": price, "low_24h": price, "open_24h": price,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "updated_ms": int(time.time() * 1000),
                }
            else:
                logger.error(f"[MarketStream] Could not seed price for {sym}!")

            for tf, limit in [("1m", 350), ("15m", 150)]:
                candles = await self._fetch_candles(sym, tf, limit)
                if candles:
                    key = f"{sym}:{tf}"
                    existing = LIVE_CANDLES.get(key, [])
                    existing_ts = {c["timestamp"] for c in existing}
                    merged = [c for c in candles if c["timestamp"] not in existing_ts] + existing
                    LIVE_CANDLES[key] = merged[-limit:]
                    logger.info(f"[MarketStream] {sym}:{tf} → {len(LIVE_CANDLES[key])} candles in cache")
                else:
                    logger.error(f"[MarketStream] Could not seed {tf} candles for {sym}!")

    async def _rest_price_loop(self) -> None:
        """Poll price every 8 s. Primary price feed when WebSocket is unavailable."""
        logger.info("[MarketStream] REST price loop started (8 s interval, multi-source)")
        fail_count = 0
        while self._running:
            for sym in self._symbols:
                price = await self._fetch_price(sym)
                if price > 0:
                    fail_count = 0
                    existing = LIVE_PRICES.get(sym, {})
                    LIVE_PRICES[sym] = {
                        **existing, "symbol": sym, "last": price,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_ms": int(time.time() * 1000),
                    }
                    # Refresh latest 1m candle
                    try:
                        new_candles = await self._fetch_candles(sym, "1m", 3)
                        if new_candles:
                            key = f"{sym}:1m"
                            candles = LIVE_CANDLES.get(key, [])
                            for c in new_candles:
                                if candles and candles[-1]["timestamp"] == c["timestamp"]:
                                    candles[-1] = c
                                elif not candles or c["timestamp"] > candles[-1]["timestamp"]:
                                    candles.append(c)
                            LIVE_CANDLES[key] = candles[-500:]
                    except Exception as e:
                        logger.warning(f"[MarketStream] Candle refresh error: {e}")
                else:
                    fail_count += 1
                    logger.error(f"[MarketStream] Price fetch failed ({fail_count} consecutive) for {sym}")
            await asyncio.sleep(8)

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("LiveMarketStreamAgent stopped")

    # ── WebSocket loop ─────────────────────────────────────────────────────────

    async def _ws_loop(self) -> None:
        while self._running:
            try:
                await self._connect_and_stream()
                self._reconnect_delay = 1.0  # reset on clean exit
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                logger.warning(
                    f"Stream disconnected: {e}. Reconnecting in {self._reconnect_delay:.0f}s"
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def _connect_and_stream(self) -> None:
        streams = self._build_stream_list()
        base = BINANCE_WS_TESTNET if self._use_testnet else BINANCE_WS_BASE
        url = f"{base}?streams={'/'.join(streams)}"

        logger.info(f"Connecting to Binance streams: {url[:80]}...")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
        ) as ws:
            self._connected = True
            self._reconnect_delay = 1.0
            logger.info(f"Connected to {len(streams)} Binance streams")

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    await self._dispatch(msg)
                except Exception as e:
                    logger.debug(f"Parse error: {e}")

        self._connected = False

    def _build_stream_list(self) -> list[str]:
        streams = []
        for sym in self._symbols:
            b = _ccxt_to_binance(sym)
            streams.append(f"{b}@ticker")           # real-time ticker
            streams.append(f"{b}@kline_1m")         # 1m candles
            streams.append(f"{b}@kline_15m")        # 15m candles (Momentum strategy)
            streams.append(f"{b}@depth20@100ms")    # order book 100ms
        return streams

    # ── Message dispatch ───────────────────────────────────────────────────────

    async def _dispatch(self, msg: dict) -> None:
        stream = msg.get("stream", "")
        data = msg.get("data", {})
        event_type = data.get("e", "")

        if event_type == "24hrTicker":
            await self._handle_ticker(data)
        elif event_type == "kline":
            await self._handle_kline(data)
        elif "@depth" in stream:
            # Binance @depth20 snapshots have NO "e" field — detect by stream name
            await self._handle_depth(stream, data)
        elif event_type in ("depthUpdate", "depth"):
            await self._handle_depth(stream, data)

    async def _handle_ticker(self, data: dict) -> None:
        raw_symbol = data.get("s", "")
        symbol = _binance_to_ccxt(raw_symbol)

        ticker = {
            "symbol": symbol,
            "last": float(data.get("c", 0)),
            "bid": float(data.get("b", 0)),
            "ask": float(data.get("a", 0)),
            "volume": float(data.get("v", 0)),         # base volume
            "quote_volume": float(data.get("q", 0)),   # USDT volume
            "change_pct": float(data.get("P", 0)),
            "high_24h": float(data.get("h", 0)),
            "low_24h": float(data.get("l", 0)),
            "open_24h": float(data.get("o", 0)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_ms": int(time.time() * 1000),
        }
        LIVE_PRICES[symbol] = ticker

        # Persist to Redis cache + broadcast to frontend
        await redis_set(f"market:ticker:{symbol}", ticker, ex=10)
        await redis_publish("market:ticker", ticker)

    async def _handle_kline(self, data: dict) -> None:
        k = data.get("k", {})
        raw_symbol = k.get("s", "")
        symbol = _binance_to_ccxt(raw_symbol)
        # Determine timeframe from the kline interval field
        interval = k.get("i", "1m")
        timeframe = interval  # "1m" or "15m"

        candle = {
            "timestamp": datetime.fromtimestamp(
                k.get("t", 0) / 1000, tz=timezone.utc
            ).isoformat(),
            "open": float(k.get("o", 0)),
            "high": float(k.get("h", 0)),
            "low": float(k.get("l", 0)),
            "close": float(k.get("c", 0)),
            "volume": float(k.get("v", 0)),
            "is_closed": k.get("x", False),
        }

        key = f"{symbol}:{timeframe}"
        if key not in LIVE_CANDLES:
            LIVE_CANDLES[key] = []

        candles = LIVE_CANDLES[key]
        if candles and candles[-1]["timestamp"] == candle["timestamp"]:
            candles[-1] = candle
        else:
            candles.append(candle)

        # Keep last 500 1m candles, 200 15m candles
        limit = 200 if timeframe == "15m" else 500
        if len(candles) > limit:
            LIVE_CANDLES[key] = candles[-limit:]

        await redis_publish("market:kline", {
            "symbol": symbol,
            "timeframe": timeframe,
            "candle": candle,
        })

    async def _handle_depth(self, stream: str, data: dict) -> None:
        # stream = "btcusdt@depth20@100ms" → extract "btcusdt"
        raw_symbol = stream.split("@")[0]  # e.g. "btcusdt"
        symbol = _binance_to_ccxt(raw_symbol.upper())  # "BTCUSDT" → "BTC/USDT"

        # Depth snapshots from @depth20 stream
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        ob = {
            "symbol": symbol,
            "bids": [{"price": float(b[0]), "amount": float(b[1])} for b in bids[:20]],
            "asks": [{"price": float(a[0]), "amount": float(a[1])} for a in asks[:20]],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        LIVE_ORDERBOOK[symbol] = ob
        await redis_set(f"market:orderbook:{symbol}", ob, ex=5)
        await redis_publish("market:orderbook", ob)

    # ── REST fallback ──────────────────────────────────────────────────────────

    async def _rest_fallback_loop(self) -> None:
        """
        When WebSockets are unavailable, poll Binance REST at 1-second intervals.
        Still uses real public endpoints — no API key required.
        """
        logger.info("REST fallback market data loop started")
        try:
            import ccxt.async_support as ccxt
            exchange = ccxt.binance({"enableRateLimit": True})
        except ImportError:
            logger.error("CCXT not available — market data unavailable")
            return

        while self._running:
            for symbol in self._symbols:
                try:
                    raw = await exchange.fetch_ticker(symbol)
                    ticker = {
                        "symbol": symbol,
                        "last": raw.get("last", 0),
                        "bid": raw.get("bid", 0),
                        "ask": raw.get("ask", 0),
                        "volume": raw.get("baseVolume", 0),
                        "quote_volume": raw.get("quoteVolume", 0),
                        "change_pct": raw.get("percentage", 0),
                        "high_24h": raw.get("high", 0),
                        "low_24h": raw.get("low", 0),
                        "open_24h": raw.get("open", 0),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "updated_ms": int(time.time() * 1000),
                    }
                    LIVE_PRICES[symbol] = ticker
                    await redis_set(f"market:ticker:{symbol}", ticker, ex=10)
                    await redis_publish("market:ticker", ticker)
                except Exception as e:
                    logger.debug(f"REST ticker error {symbol}: {e}")

            await asyncio.sleep(1)

        try:
            await exchange.close()
        except Exception:
            pass


# ── Module-level singleton ─────────────────────────────────────────────────────
_agent: Optional[LiveMarketStreamAgent] = None


def get_live_stream_agent() -> Optional[LiveMarketStreamAgent]:
    return _agent


def create_live_stream_agent(symbols: list[str] = None) -> LiveMarketStreamAgent:
    global _agent
    _agent = LiveMarketStreamAgent(symbols=symbols or DEFAULT_SYMBOLS)
    return _agent
