from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Integer, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    max_daily_loss_usd: Mapped[float] = mapped_column(Float, default=500.0)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_position_size_usd: Mapped[float] = mapped_column(Float, default=1000.0)
    max_position_size_pct: Mapped[float] = mapped_column(Float, default=10.0)
    max_leverage: Mapped[float] = mapped_column(Float, default=3.0)
    max_open_trades: Mapped[int] = mapped_column(Integer, default=5)
    max_symbol_exposure_pct: Mapped[float] = mapped_column(Float, default=20.0)
    cooldown_after_losses: Mapped[int] = mapped_column(Integer, default=3)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)
    circuit_breaker_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    circuit_breaker_threshold_pct: Mapped[float] = mapped_column(Float, default=10.0)
    symbol_blacklist: Mapped[list] = mapped_column(JSON, default=list)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
