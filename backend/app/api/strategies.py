from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Any

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.strategy import Strategy
from app.strategies import STRATEGY_REGISTRY

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class StrategyCreate(BaseModel):
    name: str
    strategy_type: str
    symbols: list[str] = ["BTC/USDT"]
    timeframe: str = "1h"
    parameters: dict[str, Any] = {}
    capital_allocation: float = 1000.0


class StrategyUpdate(BaseModel):
    mode: Optional[str] = None
    is_enabled: Optional[bool] = None
    symbols: Optional[list[str]] = None
    timeframe: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None
    capital_allocation: Optional[float] = None


@router.get("/")
async def list_strategies(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(Strategy))
    strategies = result.scalars().all()
    return [_serialize(s) for s in strategies]


@router.get("/types")
async def list_strategy_types(current_user: dict = Depends(get_current_user)):
    return [
        {
            "type": k,
            "name": v.name,
            "description": v.description,
            "default_parameters": v.default_parameters,
        }
        for k, v in STRATEGY_REGISTRY.items()
    ]


@router.post("/")
async def create_strategy(
    data: StrategyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if data.strategy_type not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown strategy type: {data.strategy_type}")

    # Merge with defaults
    cls = STRATEGY_REGISTRY[data.strategy_type]
    params = {**cls.default_parameters, **data.parameters}

    strategy = Strategy(
        name=data.name,
        strategy_type=data.strategy_type,
        symbols=data.symbols,
        timeframe=data.timeframe,
        parameters=params,
        capital_allocation=data.capital_allocation,
    )
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)
    return _serialize(strategy)


@router.get("/{strategy_id}")
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    strategy = await _get_or_404(strategy_id, db)
    return _serialize(strategy)


@router.patch("/{strategy_id}")
async def update_strategy(
    strategy_id: int,
    data: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    strategy = await _get_or_404(strategy_id, db)

    if data.mode is not None:
        if data.mode not in ("off", "paper", "live"):
            raise HTTPException(status_code=400, detail="Mode must be off/paper/live")
        strategy.mode = data.mode
        strategy.is_enabled = data.mode != "off"

    if data.is_enabled is not None:
        strategy.is_enabled = data.is_enabled
    if data.symbols is not None:
        strategy.symbols = data.symbols
    if data.timeframe is not None:
        strategy.timeframe = data.timeframe
    if data.parameters is not None:
        strategy.parameters = {**strategy.parameters, **data.parameters}
    if data.capital_allocation is not None:
        strategy.capital_allocation = data.capital_allocation

    await db.commit()
    await db.refresh(strategy)
    return _serialize(strategy)


@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    strategy = await _get_or_404(strategy_id, db)
    await db.delete(strategy)
    await db.commit()
    return {"message": "Strategy deleted"}


async def _get_or_404(strategy_id: int, db: AsyncSession) -> Strategy:
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


def _serialize(s: Strategy) -> dict:
    return {
        "id": s.id, "name": s.name, "strategy_type": s.strategy_type,
        "mode": s.mode, "status": s.status, "is_enabled": s.is_enabled,
        "symbols": s.symbols, "timeframe": s.timeframe, "parameters": s.parameters,
        "capital_allocation": s.capital_allocation, "last_signal": s.last_signal,
        "last_action": s.last_action, "consecutive_failures": s.consecutive_failures,
        "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
    }
