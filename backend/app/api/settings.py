from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from app.core.security import get_current_user
from app.core.config import settings as app_settings
from app.core.redis_client import redis_get, redis_set

router = APIRouter(prefix="/api/settings", tags=["settings"])


class NotificationSettings(BaseModel):
    email_enabled: bool = False
    email_address: Optional[str] = None
    alert_on_trade: bool = True
    alert_on_risk_block: bool = True
    alert_on_error: bool = True


@router.get("/")
async def get_settings(current_user: dict = Depends(get_current_user)):
    notif = await redis_get("settings:notifications") or {}
    trading_mode = await redis_get("settings:trading_mode") or "paper"
    return {
        "trading_mode": trading_mode,
        "allow_live_trading": bool(app_settings.allow_live_trading),
        "exchange_id": app_settings.exchange_id,
        "exchange_testnet": app_settings.exchange_testnet,
        "use_mock_exchange": bool(app_settings.use_mock_exchange),
        "notifications": notif,
    }


@router.post("/notifications")
async def update_notifications(
    data: NotificationSettings,
    current_user: dict = Depends(get_current_user),
):
    await redis_set("settings:notifications", data.model_dump(), ex=86400 * 365)
    return {"message": "Notification settings saved", "settings": data.model_dump()}


@router.post("/trading-mode")
async def set_trading_mode(
    mode: str,
    confirmed: bool = False,
    current_user: dict = Depends(get_current_user),
):
    if mode not in ("paper", "live"):
        return {"error": "Mode must be paper or live"}
    if mode == "live":
        if not app_settings.allow_live_trading:
            return {
                "error": "Live trading is disabled in environment config (ALLOW_LIVE_TRADING=0)"
            }
        if not confirmed:
            return {
                "requires_confirmation": True,
                "message": "Live trading activates real orders with real money. Set confirmed=true to proceed.",
            }
    await redis_set("settings:trading_mode", mode, ex=86400 * 365)
    return {"trading_mode": mode, "message": f"Trading mode set to {mode}"}
