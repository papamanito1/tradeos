from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.redis_client import redis_get
from app.models.position import Position
from app.models.order import Order
from app.models.trade import Trade
from app.models.strategy import Strategy

router = APIRouter(prefix="/api/overview", tags=["overview"])

PAPER_BALANCE_KEY = "paper:balance"


@router.get("/")
async def get_overview(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    state = await redis_get(PAPER_BALANCE_KEY)
    balance = state or {"balance_usd": 10000.0, "equity": 10000.0, "positions": {}}

    # Positions
    pos_result = await db.execute(select(Position).where(Position.is_open == True))
    open_positions = pos_result.scalars().all()

    # Total unrealized PnL
    unrealized_pnl = sum(p.unrealized_pnl for p in open_positions)

    # Daily PnL from Redis
    daily_pnl_raw = await redis_get("risk:daily_pnl")
    daily_pnl = float(daily_pnl_raw) if daily_pnl_raw is not None else 0.0

    # Win rate
    trades_result = await db.execute(select(Trade))
    trades = trades_result.scalars().all()
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    # Active strategies
    strat_result = await db.execute(select(Strategy).where(Strategy.is_enabled == True))
    active_strategies = strat_result.scalars().all()

    # Recent orders
    recent_orders_result = await db.execute(
        select(Order).order_by(Order.created_at.desc()).limit(10)
    )
    recent_orders = recent_orders_result.scalars().all()

    kill_switch = await redis_get("risk:kill_switch_active")

    return {
        "equity": balance.get("equity", balance.get("balance_usd", 10000)),
        "available_balance": balance.get("balance_usd", 10000),
        "unrealized_pnl": unrealized_pnl,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": (daily_pnl / balance.get("equity", 10000) * 100) if balance.get("equity", 0) else 0,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "active_strategies": len(active_strategies),
        "open_positions": len(open_positions),
        "kill_switch_active": bool(kill_switch),
        "trading_mode": "paper",
        "exchange_connected": True,
        "positions": [
            {
                "id": p.id, "symbol": p.symbol, "side": p.side, "size": p.size,
                "entry_price": p.entry_price, "current_price": p.current_price,
                "unrealized_pnl": p.unrealized_pnl,
            }
            for p in open_positions
        ],
        "recent_orders": [
            {
                "id": o.id, "symbol": o.symbol, "side": o.side, "amount": o.amount,
                "status": o.status, "created_at": o.created_at.isoformat(),
            }
            for o in recent_orders
        ],
    }
