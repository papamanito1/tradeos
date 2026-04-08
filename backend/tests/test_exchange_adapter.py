"""Tests for the exchange adapter layer."""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock

from app.exchange.base import BaseExchangeAdapter, Balance, PlacedOrder, Ticker, Candle, OrderBook
from app.exchange.paper_trading import PaperTradingEngine


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


INITIAL_STATE = {
    "balance_usd": 10000.0,
    "positions": {},
    "equity": 10000.0,
}


@pytest.fixture
def paper_engine():
    engine = PaperTradingEngine()
    return engine


class TestBaseExchangeAdapter:
    def test_is_abstract(self):
        with pytest.raises(TypeError):
            BaseExchangeAdapter()

    def test_paper_engine_implements_interface(self):
        engine = PaperTradingEngine()
        assert isinstance(engine, BaseExchangeAdapter)


class TestPaperTradingEngine:
    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock, return_value=INITIAL_STATE.copy())
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_get_balance_returns_balance(self, mock_set, mock_get, paper_engine):
        balance = run(paper_engine.get_balance())
        assert isinstance(balance, Balance)
        assert balance.total_usd > 0

    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock, return_value=INITIAL_STATE.copy())
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_place_buy_order(self, mock_set, mock_get, paper_engine):
        state = INITIAL_STATE.copy()
        state["positions"] = {}
        mock_get.return_value = state

        order = run(paper_engine.place_order("BTC/USDT", "buy", "market", 0.01))
        assert isinstance(order, PlacedOrder)
        assert order.symbol == "BTC/USDT"
        assert order.side == "buy"
        assert order.filled == 0.01
        assert order.status == "filled"
        assert order.fee > 0

    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_fetch_ticker(self, mock_set, mock_get, paper_engine):
        ticker = run(paper_engine.fetch_ticker("BTC/USDT"))
        assert isinstance(ticker, Ticker)
        assert ticker.symbol == "BTC/USDT"
        assert ticker.last > 0

    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_fetch_candles(self, mock_set, mock_get, paper_engine):
        candles = run(paper_engine.fetch_candles("BTC/USDT", "1h", 50))
        assert len(candles) == 50
        assert all(isinstance(c, Candle) for c in candles)
        assert all(c.high >= c.low for c in candles)
        assert all(c.volume > 0 for c in candles)

    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_fetch_order_book(self, mock_set, mock_get, paper_engine):
        ob = run(paper_engine.fetch_order_book("BTC/USDT", depth=10))
        assert isinstance(ob, OrderBook)
        assert len(ob.bids) == 10
        assert len(ob.asks) == 10
        assert ob.bids[0].price < ob.asks[0].price

    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock)
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_insufficient_balance_raises(self, mock_set, mock_get, paper_engine):
        state = {"balance_usd": 1.0, "positions": {}, "equity": 1.0}
        mock_get.return_value = state
        with pytest.raises(ValueError, match="Insufficient paper balance"):
            run(paper_engine.place_order("BTC/USDT", "buy", "market", 10.0))

    @patch("app.exchange.paper_trading.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.exchange.paper_trading.redis_set", new_callable=AsyncMock)
    def test_cancel_order(self, mock_set, mock_get, paper_engine):
        orders = [{"id": "abc123", "symbol": "BTC/USDT", "status": "open",
                   "order_type": "limit", "side": "buy", "amount": 0.01,
                   "filled": 0, "fee": 0}]
        mock_get.side_effect = [
            {"balance_usd": 10000, "positions": {}, "equity": 10000},
            orders,
            orders,
        ]
        result = run(paper_engine.cancel_order("abc123", "BTC/USDT"))
        assert result is True
