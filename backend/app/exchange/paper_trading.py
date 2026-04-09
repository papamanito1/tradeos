"""
Paper trading engine — simulates orders against live price data.
All state is held in Redis so it survives hot-reloads.
"""
import uuid
import random
import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.exchange.base import (
    BaseExchangeAdapter, Balance, PlacedOrder, Candle, Ticker, OrderBook, OrderBookLevel
)
from app.core.redis_client import redis_get, redis_set

PAPER_BALANCE_KEY = "paper:balance"
PAPER_ORDERS_KEY = "paper:orders"
INITIAL_BALANCE_USD = 10_000.0
MAKER_FEE = 0.001   # 0.1%
TAKER_FEE = 0.001


class PaperTradingEngine(BaseExchangeAdapter):
    """
    Simulates exchange interactions without touching real money.
    Prices are fetched from a real data source (or mocked in dev mode).
    """

    def __init__(self, price_source: Optional[BaseExchangeAdapter] = None):
        self._price_source = price_source  # real adapter for price data only

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_state(self) -> dict:
        state = await redis_get(PAPER_BALANCE_KEY)
        if state is None:
            state = {
                "balance_usd": INITIAL_BALANCE_USD,
                "positions": {},   # symbol -> {size, side, entry_price, unrealized_pnl}
                "equity": INITIAL_BALANCE_USD,
            }
            await redis_set(PAPER_BALANCE_KEY, state, ex=86400 * 365)  # 1 year
        return state

    async def _save_state(self, state: dict) -> None:
        await redis_set(PAPER_BALANCE_KEY, state, ex=86400 * 365)

    async def _get_orders(self) -> list[dict]:
        orders = await redis_get(PAPER_ORDERS_KEY)
        return orders if orders is not None else []

    async def _save_orders(self, orders: list[dict]) -> None:
        await redis_set(PAPER_ORDERS_KEY, orders, ex=86400 * 365)

    async def _get_price(self, symbol: str) -> float:
        # 1. Try live price cache (populated by LiveMarketStreamAgent — zero delay)
        try:
            from app.agents.live_market_stream import LIVE_PRICES
            cached = LIVE_PRICES.get(symbol)
            if cached and cached.get("last", 0) > 0:
                return float(cached["last"])
        except Exception:
            pass

        # 2. Try explicit price source adapter
        if self._price_source:
            try:
                ticker = await self._price_source.fetch_ticker(symbol)
                return ticker.last
            except Exception:
                pass

        # 3. Try Redis cache (set by live stream)
        try:
            from app.core.redis_client import redis_get
            cached = await redis_get(f"market:ticker:{symbol}")
            if cached and cached.get("last", 0) > 0:
                return float(cached["last"])
        except Exception:
            pass

        # 4. Bybit REST ticker (no geo-blocking)
        try:
            import httpx
            bsym = symbol.replace("/", "")
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://api.bybit.com/v5/market/tickers",
                    params={"category": "spot", "symbol": bsym},
                )
                resp.raise_for_status()
                items = resp.json().get("result", {}).get("list", [])
                if items:
                    return float(items[0].get("lastPrice", 0) or 0)
        except Exception:
            pass

        # 5. Absolute fallback: hardcoded reference prices (stale — only if all else fails)
        return self._fallback_price(symbol)

    def _fallback_price(self, symbol: str) -> float:
        """Hardcoded fallback prices — only used if all live sources fail."""
        prices = {
            "BTC/USDT": 65000.0,
            "ETH/USDT": 3200.0,
            "SOL/USDT": 145.0,
            "BNB/USDT": 580.0,
            "ADA/USDT": 0.45,
            "DOGE/USDT": 0.12,
        }
        return prices.get(symbol, 100.0)

    # ── Interface ─────────────────────────────────────────────────────────────

    async def get_balance(self) -> Balance:
        state = await self._get_state()
        # recompute unrealized PnL for all open positions
        positions_value = 0.0
        for sym, pos in state["positions"].items():
            price = await self._get_price(sym)
            size = pos["size"]
            entry = pos["entry_price"]
            side = pos["side"]
            if side == "long":
                pnl = (price - entry) * size
            else:
                pnl = (entry - price) * size
            pos["current_price"] = price
            pos["unrealized_pnl"] = pnl
            positions_value += size * price
        equity = state["balance_usd"] + sum(
            p.get("unrealized_pnl", 0) for p in state["positions"].values()
        )
        state["equity"] = equity
        await self._save_state(state)
        return Balance(
            total_usd=equity,
            available_usd=state["balance_usd"],
            positions_value=positions_value,
            assets={"USDT": state["balance_usd"]},
        )

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[PlacedOrder]:
        orders = await self._get_orders()
        result = []
        for o in orders:
            if o["status"] == "open":
                if symbol is None or o["symbol"] == symbol:
                    result.append(self._dict_to_order(o))
        return result

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> PlacedOrder:
        state = await self._get_state()
        fill_price = await self._get_price(symbol)

        # Apply taker slippage for market orders
        if order_type == "market":
            slippage = fill_price * 0.0003
            fill_price = fill_price + slippage if side == "buy" else fill_price - slippage

        cost = fill_price * amount
        fee = cost * TAKER_FEE

        if side == "buy":
            if state["balance_usd"] < cost + fee:
                raise ValueError(
                    f"Insufficient paper balance: need {cost + fee:.2f}, have {state['balance_usd']:.2f}"
                )
            state["balance_usd"] -= (cost + fee)
            pos = state["positions"].get(symbol, {"size": 0, "entry_price": 0, "side": "long"})
            if pos["size"] == 0:
                pos = {"size": amount, "entry_price": fill_price, "side": "long",
                       "stop_loss": stop_loss, "take_profit": take_profit}
            else:
                # average down
                total_size = pos["size"] + amount
                avg_entry = (pos["entry_price"] * pos["size"] + fill_price * amount) / total_size
                pos["size"] = total_size
                pos["entry_price"] = avg_entry
            state["positions"][symbol] = pos
        else:  # sell / short
            pos = state["positions"].get(symbol)
            if pos and pos["size"] >= amount:
                # close or reduce long
                realized_pnl = (fill_price - pos["entry_price"]) * amount - fee
                state["balance_usd"] += fill_price * amount - fee + realized_pnl
                if pos["size"] == amount:
                    del state["positions"][symbol]
                else:
                    pos["size"] -= amount
                    state["positions"][symbol] = pos
            else:
                # short position
                state["balance_usd"] -= fee
                state["positions"][symbol] = {
                    "size": amount, "entry_price": fill_price, "side": "short",
                    "stop_loss": stop_loss, "take_profit": take_profit,
                }

        order_id = str(uuid.uuid4())[:16]
        order_dict = {
            "id": order_id,
            "symbol": symbol,
            "order_type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "filled": amount,
            "remaining": 0.0,
            "average_fill_price": fill_price,
            "status": "filled",
            "fee": fee,
            "slippage": abs(fill_price - (price or fill_price)),
            "mode": "paper",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        orders = await self._get_orders()
        orders.append(order_dict)
        await self._save_orders(orders)
        await self._save_state(state)

        return self._dict_to_order(order_dict)

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        orders = await self._get_orders()
        for o in orders:
            if o["id"] == order_id:
                o["status"] = "cancelled"
                await self._save_orders(orders)
                return True
        return False

    # Bybit interval mapping (no geo-blocking on Railway)
    _BYBIT_TF: dict = {
        "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
        "1d": "D", "1w": "W",
    }

    async def fetch_candles(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> list[Candle]:
        """
        Fetch OHLCV candles.
        Priority: Live stream cache (1m) → Bybit REST → OKX → mock fallback.
        Avoids Binance REST which is geo-blocked on Railway (HTTP 451).
        """
        import logging
        import httpx
        log = logging.getLogger(__name__)

        # 1. Use live 1m candles from WebSocket stream cache (zero latency)
        if timeframe == "1m":
            try:
                from app.agents.live_market_stream import LIVE_CANDLES
                cached = LIVE_CANDLES.get(f"{symbol}:1m", [])
                if len(cached) >= 20:
                    return [
                        Candle(
                            timestamp=datetime.fromisoformat(c["timestamp"]),
                            open=c["open"], high=c["high"], low=c["low"],
                            close=c["close"], volume=c["volume"],
                        )
                        for c in cached[-limit:]
                    ]
            except Exception:
                pass

        # 2. Bybit REST — reliable from cloud, no geo-blocks
        try:
            bsym = symbol.replace("/", "")
            iv = self._BYBIT_TF.get(timeframe, "60")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={"category": "spot", "symbol": bsym, "interval": iv, "limit": limit},
                )
                resp.raise_for_status()
                rows = resp.json().get("result", {}).get("list", [])
                rows = list(reversed(rows))  # Bybit returns newest first
                if rows:
                    return [
                        Candle(
                            timestamp=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                            open=float(r[1]), high=float(r[2]),
                            low=float(r[3]), close=float(r[4]), volume=float(r[5]),
                        )
                        for r in rows
                    ]
        except Exception as e:
            log.warning(f"Bybit candles failed for {symbol}/{timeframe}: {e}")

        # 3. OKX fallback
        try:
            okx_tf = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H",
                      "4h": "4H", "1d": "1Dutc"}.get(timeframe, "1H")
            okx_sym = symbol.replace("/", "-")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://www.okx.com/api/v5/market/candles",
                    params={"instId": okx_sym, "bar": okx_tf, "limit": str(limit)},
                )
                resp.raise_for_status()
                rows = resp.json().get("data", [])
                rows = list(reversed(rows))
                if rows:
                    return [
                        Candle(
                            timestamp=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc),
                            open=float(r[1]), high=float(r[2]),
                            low=float(r[3]), close=float(r[4]), volume=float(r[5]),
                        )
                        for r in rows
                    ]
        except Exception as e:
            log.warning(f"OKX candles failed for {symbol}/{timeframe}: {e}")

        # 4. Last resort: mock fallback (only in offline dev mode)
        log.error(f"All candle sources failed for {symbol}/{timeframe} — using mock data")
        return self._mock_candles(symbol, timeframe, limit)

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """
        Fetch ticker.
        Priority: Live stream cache → Binance REST → fallback price.
        """
        # 1. Live stream cache (zero-delay)
        try:
            from app.agents.live_market_stream import LIVE_PRICES
            cached = LIVE_PRICES.get(symbol)
            if cached and cached.get("last", 0) > 0:
                return Ticker(
                    symbol=symbol,
                    bid=cached["bid"],
                    ask=cached["ask"],
                    last=cached["last"],
                    volume=cached["volume"],
                    change_pct=cached["change_pct"],
                )
        except Exception:
            pass

        # 2. Binance REST
        try:
            import ccxt.async_support as ccxt
            exchange = ccxt.binance({"enableRateLimit": False})
            raw = await exchange.fetch_ticker(symbol)
            await exchange.close()
            return Ticker(
                symbol=symbol,
                bid=raw.get("bid", 0),
                ask=raw.get("ask", 0),
                last=raw.get("last", 0),
                volume=raw.get("baseVolume", 0),
                change_pct=raw.get("percentage", 0) or 0,
            )
        except Exception:
            pass

        # 3. Absolute fallback
        price = self._fallback_price(symbol)
        spread = price * 0.0002
        return Ticker(
            symbol=symbol,
            bid=price - spread,
            ask=price + spread,
            last=price,
            volume=0,
            change_pct=0,
        )

    def _mock_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """Synthetic candles for dev/offline mode only."""
        from datetime import timedelta
        tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        minutes = tf_minutes.get(timeframe, 60)
        base_price = self._fallback_price(symbol)
        candles = []
        now = datetime.now(timezone.utc)
        price = base_price * 0.9
        for i in range(limit):
            ts = now - timedelta(minutes=minutes * (limit - i))
            open_p = price
            close_p = price * (1 + random.uniform(-0.015, 0.015))
            high_p = max(open_p, close_p) * (1 + random.uniform(0, 0.008))
            low_p = min(open_p, close_p) * (1 - random.uniform(0, 0.008))
            vol = random.uniform(100, 2000)
            candles.append(Candle(ts, open_p, high_p, low_p, close_p, vol))
            price = close_p
        return candles

    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        # 1. Live WebSocket order book (100ms refresh)
        try:
            from app.agents.live_market_stream import LIVE_ORDERBOOK
            cached = LIVE_ORDERBOOK.get(symbol)
            if cached:
                return OrderBook(
                    symbol=symbol,
                    bids=[OrderBookLevel(b["price"], b["amount"]) for b in cached["bids"][:depth]],
                    asks=[OrderBookLevel(a["price"], a["amount"]) for a in cached["asks"][:depth]],
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception:
            pass

        # 2. Binance REST
        try:
            import ccxt.async_support as ccxt
            exchange = ccxt.binance({"enableRateLimit": False})
            raw = await exchange.fetch_order_book(symbol, limit=depth)
            await exchange.close()
            return OrderBook(
                symbol=symbol,
                bids=[OrderBookLevel(float(b[0]), float(b[1])) for b in raw["bids"][:depth]],
                asks=[OrderBookLevel(float(a[0]), float(a[1])) for a in raw["asks"][:depth]],
                timestamp=datetime.now(timezone.utc),
            )
        except Exception:
            pass

        # 3. Synthetic fallback
        mid = await self._get_price(symbol)
        bids = [OrderBookLevel(mid * (1 - 0.0001 * i), random.uniform(0.1, 5)) for i in range(1, depth + 1)]
        asks = [OrderBookLevel(mid * (1 + 0.0001 * i), random.uniform(0.1, 5)) for i in range(1, depth + 1)]
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=datetime.now(timezone.utc))

    def _dict_to_order(self, d: dict) -> PlacedOrder:
        return PlacedOrder(
            exchange_order_id=d["id"],
            symbol=d["symbol"],
            order_type=d["order_type"],
            side=d["side"],
            amount=d["amount"],
            price=d.get("price"),
            filled=d.get("filled", 0),
            average_fill_price=d.get("average_fill_price"),
            status=d["status"],
            fee=d.get("fee", 0),
            raw=d,
        )
