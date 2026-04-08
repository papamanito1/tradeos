"""
Execution Agent
Receives signals, passes them through risk checks, and places orders.
Records every outcome in the journal.
"""
import asyncio
import logging
from typing import Optional

from app.strategies.base import Signal, SignalDirection
from app.risk.risk_engine import get_risk_engine
from app.core.redis_client import redis_publish
from app.exchange.base import BaseExchangeAdapter

logger = logging.getLogger(__name__)


class ExecutionAgent:
    def __init__(self, exchange: BaseExchangeAdapter, mode: str = "paper"):
        self._exchange = exchange
        self._mode = mode           # paper | live
        self._kill_switch = False
        self._journal_callbacks: list = []
        self._running = False

    def register_journal_callback(self, cb) -> None:
        self._journal_callbacks.append(cb)

    def set_kill_switch(self, active: bool) -> None:
        self._kill_switch = active
        if active:
            logger.critical("KILL SWITCH ACTIVATED — all execution halted")

    def set_mode(self, mode: str) -> None:
        if mode not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {mode}")
        self._mode = mode
        logger.info(f"ExecutionAgent mode set to {mode}")

    async def handle_signal(
        self,
        signal: Signal,
        strategy_id: int,
        balance_usd: float,
        open_trade_count: int,
        symbol_exposure_usd: float,
        position_size_usd: float = 100.0,
    ) -> Optional[dict]:

        if self._kill_switch:
            await self._journal("risk_block", strategy_id, signal.symbol,
                                "Kill switch active — signal rejected")
            return None

        if signal.direction == SignalDirection.none:
            return None

        price = signal.suggested_entry
        if price <= 0:
            return None

        amount = position_size_usd / price
        leverage = 1.0
        side = "buy" if signal.direction == SignalDirection.long else "sell"

        risk = get_risk_engine()
        result = await risk.check(
            symbol=signal.symbol,
            side=side,
            amount=amount,
            price=price,
            leverage=leverage,
            strategy_id=strategy_id,
            current_balance_usd=balance_usd,
            open_trade_count=open_trade_count,
            current_symbol_exposure_usd=symbol_exposure_usd,
        )

        if not result.approved:
            await self._journal("risk_block", strategy_id, signal.symbol, result.reason)
            await redis_publish("execution:risk_block", {
                "strategy_id": strategy_id,
                "symbol": signal.symbol,
                "reason": result.reason,
                "rule": result.rule,
            })
            return None

        try:
            order = await self._exchange.place_order(
                symbol=signal.symbol,
                side=side,
                order_type="market",
                amount=amount,
                stop_loss=signal.suggested_sl,
                take_profit=signal.suggested_tp,
            )
            await self._journal("trade", strategy_id, signal.symbol,
                                f"Order placed: {side} {amount:.6f} @ {order.average_fill_price}")
            await redis_publish("execution:order_placed", {
                "strategy_id": strategy_id,
                "symbol": signal.symbol,
                "side": side,
                "amount": amount,
                "fill_price": order.average_fill_price,
                "order_id": order.exchange_order_id,
                "mode": self._mode,
            })
            return {
                "order_id": order.exchange_order_id,
                "symbol": signal.symbol,
                "side": side,
                "amount": amount,
                "fill_price": order.average_fill_price,
                "fee": order.fee,
                "mode": self._mode,
            }
        except Exception as e:
            msg = f"Order placement failed: {e}"
            logger.error(msg)
            await self._journal("error", strategy_id, signal.symbol, msg)
            return None

    async def _journal(self, entry_type: str, strategy_id: int, symbol: str, message: str) -> None:
        for cb in self._journal_callbacks:
            try:
                await cb(entry_type, strategy_id, symbol, message)
            except Exception as e:
                logger.error(f"Journal callback error: {e}")
