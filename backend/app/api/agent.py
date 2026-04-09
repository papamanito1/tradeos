"""
Persistent Agent API
====================
REST endpoints for controlling and monitoring the 24/7 trading agent.
No auth required for read endpoints so the frontend can poll freely.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _get() :
    from app.agents.persistent_agent import get_agent
    return get_agent()


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Full agent state snapshot — positions, trades, log, config."""
    return _get().get_status()


# ── Control ───────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_agent():
    agent = _get()
    await agent.start()
    return {"ok": True, "message": "Agent started"}


@router.post("/stop")
async def stop_agent():
    agent = _get()
    await agent.stop()
    return {"ok": True, "message": "Agent stopped"}


class ConfigPatch(BaseModel):
    enabled:        Optional[bool]  = None
    size_usdc:      Optional[float] = None
    min_confidence: Optional[float] = None
    min_conditions: Optional[int]   = None
    mode:           Optional[str]   = None
    auto_execute:   Optional[bool]  = None


@router.post("/config")
async def update_config(patch: ConfigPatch):
    agent = _get()
    data  = {k: v for k, v in patch.model_dump().items() if v is not None}
    agent.update_config(data)
    return {"ok": True, "config": agent.config}


@router.post("/reset")
async def reset_agent():
    _get().reset()
    return {"ok": True, "message": "Paper account reset"}


@router.post("/close/{strategy_key}")
async def close_position(strategy_key: str):
    ok = _get().close_position(strategy_key)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No open position for {strategy_key}")
    return {"ok": True, "closed": strategy_key}
