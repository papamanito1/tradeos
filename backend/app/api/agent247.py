"""
24/7 Living Agent REST API
GET  /api/agent247/status     — full snapshot (config, positions, stats, log)
POST /api/agent247/config     — update config
POST /api/agent247/start      — enable + start worker
POST /api/agent247/stop       — disable + stop worker
POST /api/agent247/close/{key}— close a strategy's position manually
POST /api/agent247/reset      — wipe all paper positions + stats
GET  /api/agent247/trades     — trade history (last 100)
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.core.security import get_current_user
from app.models.user import User
from app.agent247.models import (
    Agent247Config, Agent247Position, Agent247Trade, Agent247Stats,
)
from app.agent247.worker import (
    start_worker, stop_worker, worker_running, get_log_lines,
)

router = APIRouter(prefix="/api/agent247", tags=["agent247"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ConfigPatch(BaseModel):
    enabled:        bool | None = None
    size_usdc:      float | None = None
    min_confidence: float | None = None
    min_conditions: int   | None = None
    mode:           Literal["paper"] | None = None
    auto_execute:   bool | None = None


class PositionOut(BaseModel):
    id:             str
    strategy_key:   str
    strategy_name:  str
    direction:      str
    entry:          float
    sl:             float | None
    tp:             float | None
    size_usdc:      float
    confidence:     float
    reasoning:      str | None
    opened_at:      str
    current_price:  float | None
    unrealized_pnl: float
    unrealized_pct: float
    btc_size:       float


class TradeOut(BaseModel):
    id:             str
    strategy_key:   str | None
    strategy_name:  str | None
    direction:      str
    entry:          float
    sl:             float | None
    tp:             float | None
    size_usdc:      float
    confidence:     float | None
    exit_price:     float | None
    exit_reason:    str | None
    pnl_usd:        float | None
    pnl_pct:        float | None
    status:         str
    opened_at:      str | None
    closed_at:      str | None


def _dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_create_config(db) -> Agent247Config:
    r = await db.execute(select(Agent247Config).where(Agent247Config.id == 1))
    cfg = r.scalar_one_or_none()
    if not cfg:
        cfg = Agent247Config(id=1)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg


async def _get_or_create_stats(db) -> Agent247Stats:
    r = await db.execute(select(Agent247Stats).where(Agent247Stats.id == 1))
    stats = r.scalar_one_or_none()
    if not stats:
        stats = Agent247Stats(id=1)
        db.add(stats)
        await db.commit()
        await db.refresh(stats)
    return stats


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status(current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        cfg   = await _get_or_create_config(db)
        stats = await _get_or_create_stats(db)
        positions = (await db.execute(select(Agent247Position))).scalars().all()

    open_positions = [
        PositionOut(
            id=p.id, strategy_key=p.strategy_key, strategy_name=p.strategy_name,
            direction=p.direction, entry=p.entry, sl=p.sl, tp=p.tp,
            size_usdc=p.size_usdc, confidence=p.confidence or 0,
            reasoning=p.reasoning, opened_at=_dt(p.opened_at) or "",
            current_price=p.current_price, unrealized_pnl=p.unrealized_pnl or 0,
            unrealized_pct=p.unrealized_pct or 0, btc_size=p.btc_size,
        )
        for p in positions
    ]

    return {
        "worker_running": worker_running(),
        "config": {
            "enabled":        cfg.enabled,
            "size_usdc":      cfg.size_usdc,
            "min_confidence": cfg.min_confidence,
            "min_conditions": cfg.min_conditions,
            "mode":           cfg.mode,
            "auto_execute":   cfg.auto_execute,
        },
        "open_positions": [p.model_dump() for p in open_positions],
        "stats": {
            "total_trades": stats.total_trades,
            "wins":         stats.wins,
            "losses":       stats.losses,
            "win_rate":     stats.win_rate,
            "total_pnl":    stats.total_pnl,
            "best_trade":   stats.best_trade,
            "worst_trade":  stats.worst_trade,
            "avg_rr":       stats.avg_rr,
        },
        "log": get_log_lines()[:50],
    }


@router.post("/config")
async def update_config(patch: ConfigPatch, current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        cfg = await _get_or_create_config(db)
        if patch.enabled        is not None: cfg.enabled        = patch.enabled
        if patch.size_usdc      is not None: cfg.size_usdc      = patch.size_usdc
        if patch.min_confidence is not None: cfg.min_confidence = patch.min_confidence
        if patch.min_conditions is not None: cfg.min_conditions = patch.min_conditions
        if patch.mode           is not None: cfg.mode           = patch.mode
        if patch.auto_execute   is not None: cfg.auto_execute   = patch.auto_execute
        cfg.updated_at = datetime.utcnow()
        db.add(cfg)
        await db.commit()
    return {"ok": True}


@router.post("/start")
async def start_agent(current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        cfg = await _get_or_create_config(db)
        cfg.enabled = True
        cfg.updated_at = datetime.utcnow()
        db.add(cfg)
        await db.commit()
    start_worker()
    return {"ok": True, "running": worker_running()}


@router.post("/stop")
async def stop_agent(current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        cfg = await _get_or_create_config(db)
        cfg.enabled = False
        cfg.updated_at = datetime.utcnow()
        db.add(cfg)
        await db.commit()
    stop_worker()
    return {"ok": True, "running": worker_running()}


@router.post("/close/{strategy_key}")
async def close_position(strategy_key: str, current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(Agent247Position).where(Agent247Position.strategy_key == strategy_key)
        )
        pos = r.scalar_one_or_none()
        if not pos:
            raise HTTPException(404, f"No open position for strategy '{strategy_key}'")

    from app.agent247.worker import _close_position
    await _close_position(pos, pos.current_price or pos.entry, "manual")
    return {"ok": True}


@router.post("/reset")
async def reset_account(current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Agent247Position))
        await db.execute(delete(Agent247Stats))
        db.add(Agent247Stats(id=1))
        await db.commit()
    return {"ok": True}


@router.get("/trades")
async def get_trades(limit: int = 100, current_user: User = Depends(get_current_user)):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Agent247Trade).order_by(Agent247Trade.closed_at.desc()).limit(limit)
        )).scalars().all()

    return [
        TradeOut(
            id=t.id, strategy_key=t.strategy_key, strategy_name=t.strategy_name,
            direction=t.direction, entry=t.entry, sl=t.sl, tp=t.tp,
            size_usdc=t.size_usdc, confidence=t.confidence,
            exit_price=t.exit_price, exit_reason=t.exit_reason,
            pnl_usd=t.pnl_usd, pnl_pct=t.pnl_pct,
            status=t.status, opened_at=_dt(t.opened_at), closed_at=_dt(t.closed_at),
        )
        for t in rows
    ]
