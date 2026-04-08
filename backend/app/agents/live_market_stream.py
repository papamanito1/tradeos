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
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"]
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
        if HAS_WEBSOCKETS:
            self._tasks.append(asyncio.create_task(self._ws_loop()))
        else:
            logger.warning("websockets library not available — using REST fallback")
            self._tasks.append(asyncio.create_task(self._rest_fallback_loop()))
        logger.info(f"LiveMarketStreamAgent started for {self._symbols}")

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
        timeframe = "1m"

        candle = {
            "timestamp": datetime.fromtimestamp(
                k.get("t", 0) / 1000, tz=timezone.utc
            ).isoformat(),
            "open": float(k.get("o", 0)),
            "high": float(k.get("h", 0)),
            "low": float(k.get("l", 0)),
            "close": float(k.get("c", 0)),
            "volume": float(k.get("v", 0)),
            "is_closed": k.get("x", False),  # True when candle finalized
        }

        key = f"{symbol}:{timeframe}"
        if key not in LIVE_CANDLES:
            LIVE_CANDLES[key] = []

        candles = LIVE_CANDLES[key]
        if candles and candles[-1]["timestamp"] == candle["timestamp"]:
            # Update the current (open) candle in-place
            candles[-1] = candle
        else:
            candles.append(candle)

        # Keep last 500 candles
        if len(candles) > 500:
            LIVE_CANDLES[key] = candles[-500:]

        await redis_publish("market:kline", {
            "symbol": symbol,
            "timeframe": timeframe,
            "candle": candle,
        })

    async def _handle_depth(self, stream: str, data: dict) -> None:
        # Extract symbol from stream name "btcusdt@depth20@100ms"
        raw_symbol = stream.split("@")[0].upper() + "USDT"
        if "@" in stream:
            raw_symbol = stream.split("@")[0]
        symbol = _binance_to_ccxt(raw_symbol.upper().replace("USDT", "") + "USDT")

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
