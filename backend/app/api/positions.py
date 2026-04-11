from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.redis_client import redis_get, redis_set
from app.models.position import Position

router = APIRouter(prefix="/api/positions", tags=["positions"])

PAPER_BALANCE_KEY = "paper:balance"


@router.get("/")
async def list_positions(
    open_only: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = select(Position)
    if open_only:
        query = query.where(Position.is_open == True)
    result = await db.execute(query.order_by(Position.opened_at.desc()))
    positions = result.scalars().all()

    # Enrich with live prices from Redis
    state = await redis_get(PAPER_BALANCE_KEY)
    paper_positions = state.get("positions", {}) if state else {}

    return [_serialize(p, paper_positions.get(p.symbol, {})) for p in positions]


@router.get("/{position_id}")
async def get_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    return _serialize(pos, {})


class ClosePositionRequest(BaseModel):
    reduce_pct: float = 100.0  # 100 = full close


@router.post("/{position_id}/close")
async def close_position(
    position_id: int,
    req: ClosePositionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(Position).where(Position.id == position_id))
    pos = result.scalar_one_or_none()
    if not pos or not pos.is_open:
        raise HTTPException(status_code=404, detail="Open position not found")

    # For paper trading: close in Redis state
    state = await redis_get(PAPER_BALANCE_KEY)
    if state and pos.symbol in state.get("positions", {}):
        paper_pos = state["positions"][pos.symbol]
        reduce_amount = paper_pos["size"] * (req.reduce_pct / 100)
        current_price = paper_pos.get("current_price", pos.entry_price)
        entry_price   = paper_pos.get("entry_price", pos.entry_price)
        side          = paper_pos.get("side", "long")
        realized      = (current_price - entry_price) * reduce_amount
        if side == "short":
            realized = -realized
        # Return the original margin (entry_price * size) plus PnL — not current_price * size + PnL
        state["balance_usd"] = state.get("balance_usd", 10000) + entry_price * reduce_amount + realized

        if req.reduce_pct >= 100:
            del state["positions"][pos.symbol]
            from datetime import datetime, timezone
            pos.is_open = False
            pos.closed_at = datetime.now(timezone.utc)
            pos.realized_pnl = realized
        else:
            paper_pos["size"] -= reduce_amount
            state["positions"][pos.symbol] = paper_pos

        await redis_set(PAPER_BALANCE_KEY, state, ex=86400 * 365)
        await db.commit()

    return {"message": f"Position {'closed' if req.reduce_pct >= 100 else 'reduced'}", "position_id": position_id}


def _serialize(p: Position, live_data: dict) -> dict:
    return {
        "id": p.id, "strategy_id": p.strategy_id, "symbol": p.symbol,
        "side": p.side, "size": p.size, "entry_price": p.entry_price,
        "current_price": live_data.get("current_price", p.current_price),
        "unrealized_pnl": live_data.get("unrealized_pnl", p.unrealized_pnl),
        "realized_pnl": p.realized_pnl, "stop_loss": p.stop_loss,
        "take_profit": p.take_profit, "leverage": p.leverage, "mode": p.mode,
        "is_open": p.is_open, "opened_at": p.opened_at.isoformat(),
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
    }
