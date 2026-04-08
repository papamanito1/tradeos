"""
Risk Agent
Monitors running positions and enforces stop-loss / take-profit rules.
Triggers kill switch if system-level thresholds are breached.
"""
import asyncio
import logging

from app.exchange.base import BaseExchangeAdapter
from app.risk.risk_engine import get_risk_engine
from app.core.redis_client import redis_get, redis_publish, redis_set

logger = logging.getLogger(__name__)

PAPER_BALANCE_KEY = "paper:balance"


class RiskAgent:
    def __init__(self, exchange: BaseExchangeAdapter, execution_agent):
        self._exchange = exchange
        self._execution = execution_agent
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._running = True
        self._tasks.append(asyncio.create_task(self._monitor_loop()))
        logger.info("RiskAgent started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self._check_positions()
                await self._check_daily_limits()
            except Exception as e:
                logger.error(f"RiskAgent monitor error: {e}")
            await asyncio.sleep(10)

    async def _check_positions(self) -> None:
        state = await redis_get(PAPER_BALANCE_KEY)
        if not state:
            return

        risk = get_risk_engine()
        for symbol, pos in list(state.get("positions", {}).items()):
            current_price = pos.get("current_price", pos["entry_price"])
            entry = pos["entry_price"]
            side = pos.get("side", "long")
            sl = pos.get("stop_loss")
            tp = pos.get("take_profit")

            hit_sl = sl and (
                (side == "long" and current_price <= sl) or
                (side == "short" and current_price >= sl)
            )
            hit_tp = tp and (
                (side == "long" and current_price >= tp) or
                (side == "short" and current_price <= tp)
            )

            if hit_sl or hit_tp:
                reason = "stop_loss" if hit_sl else "take_profit"
                logger.info(f"Auto-close {symbol}: {reason} triggered at {current_price}")
                await redis_publish("risk:position_closed", {
                    "symbol": symbol, "reason": reason, "price": current_price,
                })

    async def _check_daily_limits(self) -> None:
        risk = get_risk_engine()
        daily_pnl = await risk.get_daily_pnl()
        config = risk.config

        if daily_pnl < -config.max_daily_loss_usd and not config.kill_switch_active:
            logger.critical(f"Daily loss limit hit: {daily_pnl:.2f}. Activating kill switch.")
            config.kill_switch_active = True
            self._execution.set_kill_switch(True)
            await redis_publish("risk:kill_switch", {"reason": "daily_loss_limit", "pnl": daily_pnl})
            await redis_set("risk:kill_switch_active", True, ex=86400)
