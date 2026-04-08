from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    change_pct: float


@dataclass
class OrderBookLevel:
    price: float
    amount: float


@dataclass
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime


@dataclass
class PlacedOrder:
    exchange_order_id: str
    symbol: str
    order_type: str
    side: str
    amount: float
    price: Optional[float]
    filled: float
    average_fill_price: Optional[float]
    status: str
    fee: float
    raw: dict = field(default_factory=dict)


@dataclass
class Balance:
    total_usd: float
    available_usd: float
    positions_value: float
    assets: dict[str, float] = field(default_factory=dict)


class BaseExchangeAdapter(ABC):
    """
    Abstract interface for all exchange integrations.
    New exchanges must implement every method here.
    """

    @abstractmethod
    async def get_balance(self) -> Balance:
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[PlacedOrder]:
        ...

    @abstractmethod
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
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        ...

    @abstractmethod
    async def fetch_candles(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> list[Candle]:
        ...

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker:
        ...

    @abstractmethod
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        ...
