from datetime import datetime, timezone
from typing import Any
from sqlalchemy import String, Boolean, DateTime, JSON, Float, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.core.database import Base


class StrategyMode(str, enum.Enum):
    off = "off"
    paper = "paper"
    live = "live"


class StrategyStatus(str, enum.Enum):
    idle = "idle"
    running = "running"
    error = "error"
    stopped = "stopped"


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default=StrategyMode.off)
    status: Mapped[str] = mapped_column(String(16), default=StrategyStatus.idle)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(16), default="1h")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    capital_allocation: Mapped[float] = mapped_column(Float, default=1000.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_signal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_action: Mapped[str | None] = mapped_column(String(256), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
