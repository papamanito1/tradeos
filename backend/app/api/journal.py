from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.journal import JournalEntry

router = APIRouter(prefix="/api/journal", tags=["journal"])


class ManualEntryRequest(BaseModel):
    message: str
    details: Optional[str] = None
    symbol: Optional[str] = None


@router.get("/")
async def list_entries(
    entry_type: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    query = select(JournalEntry).order_by(desc(JournalEntry.created_at))
    if entry_type:
        query = query.where(JournalEntry.entry_type == entry_type)
    if level:
        query = query.where(JournalEntry.level == level)
    if symbol:
        query = query.where(JournalEntry.symbol == symbol)
    if search:
        # Escape SQL wildcard chars to prevent pattern abuse / full-table scans
        safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(JournalEntry.message.ilike(f"%{safe_search}%", escape="\\"))
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()
    return [_serialize(e) for e in entries]


@router.post("/manual")
async def add_manual_entry(
    req: ManualEntryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = JournalEntry(
        entry_type="manual",
        level="info",
        message=req.message,
        details=req.details,
        symbol=req.symbol,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _serialize(entry)


def _serialize(e: JournalEntry) -> dict:
    return {
        "id": e.id, "entry_type": e.entry_type, "level": e.level,
        "strategy_id": e.strategy_id, "symbol": e.symbol,
        "message": e.message, "details": e.details,
        "created_at": e.created_at.isoformat(),
    }
