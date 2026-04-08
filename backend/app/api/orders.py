from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.order import Order

router = APIRouter(prefix="/api/orders", tags=["orders"])


class ManualOrderRequest(BaseModel):
    symbol: str
    side: str   # buy | sell
    order_type: str = "market"
    amount: float
    price: Optional[float] = None


@router.get("/")
async def list_orders(
    status: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if status:
        query = query.where(Order.status == status)
    if symbol:
        query = query.where(Order.symbol == symbol)
    result = await db.execute(query)
    orders = result.scalars().all()
    return [_serialize(o) for o in orders]


@router.get("/{order_id}")
async def get_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return _serialize(order)


@router.post("/manual")
async def place_manual_order(
    req: ManualOrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Place a manual paper order bypassing strategy signals."""
    from app.exchange.paper_trading import PaperTradingEngine
    from app.risk.risk_engine import get_risk_engine
    from app.core.redis_client import redis_get

    state = await redis_get("paper:balance")
    balance = state.get("balance_usd", 10000) if state else 10000
    open_positions = len(state.get("positions", {})) if state else 0

    risk = get_risk_engine()
    result = await risk.check(
        symbol=req.symbol, side=req.side, amount=req.amount,
        price=req.price or 0, leverage=1.0, strategy_id=None,
        current_balance_usd=balance, open_trade_count=open_positions,
        current_symbol_exposure_usd=0,
    )
    if not result.approved:
        raise HTTPException(status_code=400, detail=f"Risk check failed: {result.reason}")

    engine = PaperTradingEngine()
    placed = await engine.place_order(
        symbol=req.symbol, side=req.side, order_type=req.order_type,
        amount=req.amount, price=req.price,
    )

    order = Order(
        exchange_order_id=placed.exchange_order_id,
        symbol=req.symbol, order_type=req.order_type, side=req.side,
        amount=req.amount, price=req.price,
        filled=placed.filled, average_fill_price=placed.average_fill_price,
        status=placed.status, fee=placed.fee, mode="paper",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return _serialize(order)


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order or order.status != "open":
        raise HTTPException(status_code=404, detail="Open order not found")
    order.status = "cancelled"
    await db.commit()
    return {"message": "Order cancelled"}


def _serialize(o: Order) -> dict:
    return {
        "id": o.id, "exchange_order_id": o.exchange_order_id, "strategy_id": o.strategy_id,
        "symbol": o.symbol, "order_type": o.order_type, "side": o.side, "amount": o.amount,
        "price": o.price, "filled": o.filled, "remaining": o.remaining,
        "average_fill_price": o.average_fill_price, "status": o.status, "mode": o.mode,
        "fee": o.fee, "slippage": o.slippage, "error_message": o.error_message,
        "created_at": o.created_at.isoformat(), "updated_at": o.updated_at.isoformat(),
    }
