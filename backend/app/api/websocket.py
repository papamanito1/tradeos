import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError

from app.core.security import decode_token
from app.core.redis_client import redis_get
from app.websockets.manager import manager

router = APIRouter(tags=["websocket"])

PAPER_BALANCE_KEY = "paper:balance"


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    try:
        payload = decode_token(token)
        if not payload.get("sub"):
            await ws.close(code=4001)
            return
    except JWTError:
        await ws.close(code=4001)
        return

    await manager.connect(ws)
    try:
        # Send initial snapshot on connect
        state = await redis_get(PAPER_BALANCE_KEY)
        if state:
            await manager.send_to(ws, "snapshot", {
                "balance": state.get("balance_usd"),
                "equity": state.get("equity"),
                "positions": state.get("positions", {}),
            })

        while True:
            # Keep connection alive, receive pings
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30)
                if data == "ping":
                    await ws.send_text(json.dumps({"event": "pong"}))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"event": "heartbeat"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
