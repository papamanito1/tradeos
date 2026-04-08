"""
CCXT-based exchange adapter for live trading.
Supports Binance testnet by default.
"""
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt

from app.exchange.base import (
    BaseExchangeAdapter, Balance, PlacedOrder, Candle, Ticker, OrderBook, OrderBookLevel
)
from app.core.config import settings


class CCXTAdapter(BaseExchangeAdapter):
    def __init__(
        self,
        exchange_id: str = None,
        api_key: str = None,
        api_secret: str = None,
        testnet: bool = True,
    ):
        exchange_id = exchange_id or settings.exchange_id
        api_key = api_key or settings.exchange_api_key
        api_secret = api_secret or settings.exchange_api_secret
        testnet = testnet if testnet is not None else settings.exchange_testnet

        exchange_class = getattr(ccxt, exchange_id)
        self._exchange: ccxt.Exchange = exchange_class(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future" if testnet else "spot"},
            }
        )
        if testnet and hasattr(self._exchange, "set_sandbox_mode"):
            self._exchange.set_sandbox_mode(True)

    async def close(self):
        await self._exchange.close()

    async def get_balance(self) -> Balance:
        raw = await self._exchange.fetch_balance()
        usdt = raw.get("USDT", {})
        total = float(usdt.get("total", 0))
        free = float(usdt.get("free", 0))
        return Balance(
            total_usd=total,
            available_usd=free,
            positions_value=total - free,
            assets={k: float(v.get("total", 0)) for k, v in raw.items() if isinstance(v, dict)},
        )

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[PlacedOrder]:
        raw_orders = await self._exchange.fetch_open_orders(symbol)
        return [self._parse_order(o) for o in raw_orders]

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
        params = {}
        raw = await self._exchange.create_order(symbol, order_type, side, amount, price, params)
        return self._parse_order(raw)

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception:
            return False

    async def fetch_candles(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> list[Candle]:
        raw = await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [
            Candle(
                timestamp=datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
                open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5],
            )
            for c in raw
        ]

    async def fetch_ticker(self, symbol: str) -> Ticker:
        raw = await self._exchange.fetch_ticker(symbol)
        return Ticker(
            symbol=symbol,
            bid=raw.get("bid", 0),
            ask=raw.get("ask", 0),
            last=raw.get("last", 0),
            volume=raw.get("baseVolume", 0),
            change_pct=raw.get("percentage", 0),
        )

    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self._exchange.fetch_order_book(symbol, limit=depth)
        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(b[0], b[1]) for b in raw["bids"][:depth]],
            asks=[OrderBookLevel(a[0], a[1]) for a in raw["asks"][:depth]],
            timestamp=datetime.now(timezone.utc),
        )

    def _parse_order(self, raw: dict) -> PlacedOrder:
        fee_cost = 0.0
        if raw.get("fee"):
            fee_cost = float(raw["fee"].get("cost", 0))
        return PlacedOrder(
            exchange_order_id=str(raw.get("id", "")),
            symbol=raw.get("symbol", ""),
            order_type=raw.get("type", "market"),
            side=raw.get("side", "buy"),
            amount=float(raw.get("amount", 0)),
            price=raw.get("price"),
            filled=float(raw.get("filled", 0)),
            average_fill_price=raw.get("average"),
            status=raw.get("status", "open"),
            fee=fee_cost,
            raw=raw,
        )
