from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.redis_client import redis_get, redis_set
from app.models.risk_settings import RiskSettings
from app.risk.risk_engine import RiskConfig, update_risk_engine, get_risk_engine

router = APIRouter(prefix="/api/risk", tags=["risk"])


class RiskSettingsUpdate(BaseModel):
    max_daily_loss_usd: Optional[float] = None
    max_daily_loss_pct: Optional[float] = None
    max_position_size_usd: Optional[float] = None
    max_position_size_pct: Optional[float] = None
    max_leverage: Optional[float] = None
    max_open_trades: Optional[int] = None
    max_symbol_exposure_pct: Optional[float] = None
    cooldown_after_losses: Optional[int] = None
    cooldown_minutes: Optional[int] = None
    circuit_breaker_enabled: Optional[bool] = None
    circuit_breaker_threshold_pct: Optional[float] = None
    symbol_blacklist: Optional[list[str]] = None


@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(RiskSettings).limit(1))
    rs = result.scalar_one_or_none()
    if not rs:
        rs = RiskSettings()
        db.add(rs)
        await db.commit()
        await db.refresh(rs)
    kill_switch = await redis_get("risk:kill_switch_active")
    return {**_serialize(rs), "kill_switch_active": bool(kill_switch)}


@router.patch("/settings")
async def update_settings(
    data: RiskSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(RiskSettings).limit(1))
    rs = result.scalar_one_or_none()
    if not rs:
        rs = RiskSettings()
        db.add(rs)

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(rs, field, value)

    await db.commit()
    await db.refresh(rs)

    # Reload risk engine with new config
    update_risk_engine(RiskConfig(
        max_daily_loss_usd=rs.max_daily_loss_usd,
        max_daily_loss_pct=rs.max_daily_loss_pct,
        max_position_size_usd=rs.max_position_size_usd,
        max_position_size_pct=rs.max_position_size_pct,
        max_leverage=rs.max_leverage,
        max_open_trades=rs.max_open_trades,
        max_symbol_exposure_pct=rs.max_symbol_exposure_pct,
        cooldown_after_losses=rs.cooldown_after_losses,
        cooldown_minutes=rs.cooldown_minutes,
        circuit_breaker_enabled=rs.circuit_breaker_enabled,
        circuit_breaker_threshold_pct=rs.circuit_breaker_threshold_pct,
        symbol_blacklist=rs.symbol_blacklist,
    ))
    return _serialize(rs)


@router.post("/kill-switch")
async def toggle_kill_switch(
    active: bool,
    current_user: dict = Depends(get_current_user),
):
    engine = get_risk_engine()
    engine.config.kill_switch_active = active
    await redis_set("risk:kill_switch_active", active, ex=86400)
    return {"kill_switch_active": active, "message": "Kill switch " + ("activated" if active else "deactivated")}


@router.get("/status")
async def get_risk_status(current_user: dict = Depends(get_current_user)):
    engine = get_risk_engine()
    daily_pnl = await engine.get_daily_pnl()
    kill_switch = await redis_get("risk:kill_switch_active")
    return {
        "daily_pnl": daily_pnl,
        "kill_switch_active": bool(kill_switch),
        "config": {
            "max_daily_loss_usd": engine.config.max_daily_loss_usd,
            "max_open_trades": engine.config.max_open_trades,
            "max_leverage": engine.config.max_leverage,
        },
    }


def _serialize(rs: RiskSettings) -> dict:
    return {
        "id": rs.id,
        "max_daily_loss_usd": rs.max_daily_loss_usd,
        "max_daily_loss_pct": rs.max_daily_loss_pct,
        "max_position_size_usd": rs.max_position_size_usd,
        "max_position_size_pct": rs.max_position_size_pct,
        "max_leverage": rs.max_leverage,
        "max_open_trades": rs.max_open_trades,
        "max_symbol_exposure_pct": rs.max_symbol_exposure_pct,
        "cooldown_after_losses": rs.cooldown_after_losses,
        "cooldown_minutes": rs.cooldown_minutes,
        "circuit_breaker_enabled": rs.circuit_breaker_enabled,
        "circuit_breaker_threshold_pct": rs.circuit_breaker_threshold_pct,
        "symbol_blacklist": rs.symbol_blacklist,
        "updated_at": rs.updated_at.isoformat(),
    }
