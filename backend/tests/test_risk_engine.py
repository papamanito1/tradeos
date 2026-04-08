"""Tests for the risk engine."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from app.risk.risk_engine import RiskEngine, RiskConfig, RiskCheckResult


@pytest.fixture
def default_config():
    return RiskConfig(
        max_daily_loss_usd=500,
        max_daily_loss_pct=5.0,
        max_position_size_usd=1000,
        max_position_size_pct=10.0,
        max_leverage=3.0,
        max_open_trades=5,
        max_symbol_exposure_pct=20.0,
        cooldown_after_losses=3,
        cooldown_minutes=60,
        circuit_breaker_enabled=True,
        circuit_breaker_threshold_pct=10.0,
        symbol_blacklist=[],
        kill_switch_active=False,
    )


@pytest.fixture
def engine(default_config):
    return RiskEngine(default_config)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


COMMON_ARGS = dict(
    symbol="BTC/USDT",
    side="buy",
    amount=0.01,
    price=65000.0,
    leverage=1.0,
    strategy_id=1,
    current_balance_usd=10000.0,
    open_trade_count=0,
    current_symbol_exposure_usd=0.0,
)


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_approved_trade(mock_redis, engine):
    result = run(engine.check(**COMMON_ARGS))
    assert result.approved is True
    assert "passed" in result.reason


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_kill_switch_blocks_all(mock_redis, engine):
    engine.config.kill_switch_active = True
    result = run(engine.check(**COMMON_ARGS))
    assert result.approved is False
    assert result.rule == "kill_switch"


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_blacklisted_symbol(mock_redis, engine):
    engine.config.symbol_blacklist = ["BTC/USDT"]
    result = run(engine.check(**COMMON_ARGS))
    assert result.approved is False
    assert result.rule == "symbol_blacklist"


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_leverage_exceeded(mock_redis, engine):
    args = {**COMMON_ARGS, "leverage": 5.0}
    result = run(engine.check(**args))
    assert result.approved is False
    assert result.rule == "max_leverage"


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_max_open_trades(mock_redis, engine):
    args = {**COMMON_ARGS, "open_trade_count": 5}
    result = run(engine.check(**args))
    assert result.approved is False
    assert result.rule == "max_open_trades"


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_position_size_usd_exceeded(mock_redis, engine):
    # amount=0.02, price=65000 → notional=1300 > max=1000
    args = {**COMMON_ARGS, "amount": 0.02, "price": 65000.0}
    result = run(engine.check(**args))
    assert result.approved is False
    assert result.rule == "max_position_size_usd"


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=-600.0)
def test_daily_loss_exceeded(mock_redis, engine):
    result = run(engine.check(**COMMON_ARGS))
    assert result.approved is False
    assert result.rule == "max_daily_loss_usd"


@patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, return_value=None)
def test_circuit_breaker(mock_redis, engine):
    # daily PnL mocked via separate mock in engine
    engine.config.circuit_breaker_threshold_pct = 5.0
    # mock _get_daily_pnl
    import asyncio

    async def fake_daily_pnl(key):
        if "daily_pnl" in key:
            return -600.0  # 6% of 10k
        return None

    with patch("app.risk.risk_engine.redis_get", new_callable=AsyncMock, side_effect=fake_daily_pnl):
        result = run(engine.check(**COMMON_ARGS))
    assert result.approved is False
    assert result.rule in ("max_daily_loss_usd", "circuit_breaker")
