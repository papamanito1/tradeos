"""
SQLAlchemy models for the 24/7 server-side living agent.
All tables are prefixed agent247_ to avoid conflicts.
"""

from datetime import datetime
from sqlalchemy import Boolean, Column, Float, Integer, String, DateTime, Text
from app.core.database import Base


class Agent247Config(Base):
    __tablename__ = "agent247_config"

    id              = Column(Integer, primary_key=True, default=1)
    enabled         = Column(Boolean, default=False)
    size_usdc       = Column(Float,   default=100.0)
    min_confidence  = Column(Float,   default=0.50)
    min_conditions  = Column(Integer, default=3)
    mode            = Column(String,  default="paper")   # paper only for now
    auto_execute    = Column(Boolean, default=True)
    updated_at      = Column(DateTime, default=datetime.utcnow)


class Agent247Position(Base):
    __tablename__ = "agent247_positions"

    id              = Column(String, primary_key=True)
    strategy_key    = Column(String, nullable=False)   # "momentum" | "obi" | "orb"
    strategy_name   = Column(String, nullable=False)
    direction       = Column(String, nullable=False)   # "long" | "short"
    entry           = Column(Float,  nullable=False)
    sl              = Column(Float)
    tp              = Column(Float)
    size_usdc       = Column(Float,  nullable=False)
    confidence      = Column(Float,  default=0)
    reasoning       = Column(Text)
    opened_at       = Column(DateTime, default=datetime.utcnow)
    current_price   = Column(Float)
    unrealized_pnl  = Column(Float, default=0)
    unrealized_pct  = Column(Float, default=0)
    btc_size        = Column(Float, nullable=False)


class Agent247Trade(Base):
    __tablename__ = "agent247_trades"

    id              = Column(String, primary_key=True)
    strategy_key    = Column(String)
    strategy_name   = Column(String)
    direction       = Column(String, nullable=False)
    entry           = Column(Float,  nullable=False)
    sl              = Column(Float)
    tp              = Column(Float)
    size_usdc       = Column(Float,  nullable=False)
    confidence      = Column(Float,  default=0)
    reasoning       = Column(Text)
    is_paper        = Column(Boolean, default=True)
    exit_price      = Column(Float)
    exit_reason     = Column(String)   # "tp" | "sl" | "manual"
    pnl_usd         = Column(Float)
    pnl_pct         = Column(Float)
    status          = Column(String, default="confirmed")
    opened_at       = Column(DateTime)
    closed_at       = Column(DateTime)


class Agent247Stats(Base):
    __tablename__ = "agent247_stats"

    id              = Column(Integer, primary_key=True, default=1)
    total_trades    = Column(Integer, default=0)
    wins            = Column(Integer, default=0)
    losses          = Column(Integer, default=0)
    win_rate        = Column(Float,   default=0)
    total_pnl       = Column(Float,   default=0)
    best_trade      = Column(Float,   default=0)
    worst_trade     = Column(Float,   default=0)
    avg_rr          = Column(Float,   default=0)
