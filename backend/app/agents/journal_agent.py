"""
Journal Agent
Persists all system events: signals, trades, errors, risk blocks.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal import JournalEntry
from app.core.redis_client import redis_publish

logger = logging.getLogger(__name__)


class JournalAgent:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def log(
        self,
        entry_type: str,
        message: str,
        level: str = "info",
        strategy_id: int | None = None,
        symbol: str | None = None,
        details: str | None = None,
    ) -> None:
        try:
            async with self._session_factory() as session:
                entry = JournalEntry(
                    entry_type=entry_type,
                    level=level,
                    strategy_id=strategy_id,
                    symbol=symbol,
                    message=message,
                    details=details,
                )
                session.add(entry)
                await session.commit()

            await redis_publish("journal:new", {
                "entry_type": entry_type,
                "level": level,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "message": message,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.error(f"JournalAgent failed to log: {e}")

    async def log_signal(self, strategy_id: int, symbol: str, direction: str, reasoning: str) -> None:
        await self.log(
            "signal", f"[{direction.upper()}] {symbol} — {reasoning}",
            strategy_id=strategy_id, symbol=symbol,
        )

    async def log_trade(self, strategy_id: int, symbol: str, side: str, amount: float,
                        price: float, mode: str) -> None:
        await self.log(
            "trade",
            f"{mode.upper()} | {side.upper()} {amount:.6f} {symbol} @ {price:.4f}",
            strategy_id=strategy_id, symbol=symbol,
        )

    async def log_error(self, message: str, strategy_id: int | None = None,
                        symbol: str | None = None) -> None:
        await self.log("error", message, level="error", strategy_id=strategy_id, symbol=symbol)

    async def log_risk_block(self, reason: str, strategy_id: int | None = None,
                             symbol: str | None = None) -> None:
        await self.log(
            "risk_block", f"BLOCKED: {reason}", level="warning",
            strategy_id=strategy_id, symbol=symbol,
        )
