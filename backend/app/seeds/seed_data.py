"""
Seed script — creates admin user, demo strategies, and sample journal entries.
Run: python -m app.seeds.seed_data
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, init_db
from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User
from app.models.strategy import Strategy
from app.models.risk_settings import RiskSettings
from app.models.journal import JournalEntry


async def seed():
    print("Initializing database schema...")
    await init_db()

    async with AsyncSessionLocal() as session:
        # ── Admin user ─────────────────────────────────────────────────────────
        result = await session.execute(select(User).where(User.username == settings.admin_username))
        existing = result.scalar_one_or_none()
        if not existing:
            hashed = hash_password(settings.admin_password)
            admin = User(
                username=settings.admin_username,
                hashed_password=hashed,
                is_active=True,
                is_admin=True,
            )
            session.add(admin)
            print(f"Created admin user: {settings.admin_username} (hash prefix: {hashed[:10]})")
        else:
            # Always update password hash on startup so credential changes take effect
            existing.hashed_password = hash_password(settings.admin_password)
            print(f"Updated admin user password: {settings.admin_username}")

        # ── Risk settings ──────────────────────────────────────────────────────
        result = await session.execute(select(RiskSettings).limit(1))
        if not result.scalar_one_or_none():
            rs = RiskSettings()
            session.add(rs)
            print("Created default risk settings.")

        # ── Remove all strategies except BTC Momentum Velocity ────────────────
        keep_name = "BTC Momentum Velocity 15m"
        all_strats = await session.execute(select(Strategy))
        for s in all_strats.scalars().all():
            if s.name != keep_name:
                await session.delete(s)
                print(f"Removed strategy: {s.name}")

        # ── Ensure BTC Momentum Velocity 15m exists and is enabled ────────────
        result = await session.execute(select(Strategy).where(Strategy.name == keep_name))
        existing_mv = result.scalar_one_or_none()
        mv_params = {
            "ema_trend_period":    50,
            "ema_pullback_period": 21,
            "rsi_period":          14,
            "atr_period":          14,
            "vwap_window":         50,
            "vol_avg_period":      20,
            "rsi_cross_lookback":  3,
            "rsi_trigger_long":    52,
            "rsi_trigger_short":   48,
            "vol_ratio_min":       1.4,
            "body_ratio_min":      0.50,
            "pullback_atr_mult":   1.2,
            "sl_atr_mult":         1.5,
            "tp_atr_mult":         3.0,
            "atr_min_pct":         0.001,
            "atr_max_pct":         0.012,
            "ema_slope_bars":      5,
        }
        if not existing_mv:
            session.add(Strategy(
                name=keep_name,
                strategy_type="btc_momentum_velocity",
                symbols=["BTC/USDT"],
                timeframe="15m",
                parameters=mv_params,
                capital_allocation=10000.0,
                mode="paper",
                is_enabled=True,
            ))
            print(f"Created strategy: {keep_name}")
        else:
            existing_mv.is_enabled = True
            existing_mv.mode = "paper"
            existing_mv.capital_allocation = 10000.0
            existing_mv.parameters = mv_params
            print(f"Updated strategy: {keep_name}")

        # ── Sample journal entries ─────────────────────────────────────────────
        result = await session.execute(select(JournalEntry).limit(1))
        if not result.scalar_one_or_none():
            entries = [
                JournalEntry(entry_type="system", level="info", message="TradeOS initialized"),
                JournalEntry(entry_type="system", level="info", message="Paper trading mode active"),
                JournalEntry(entry_type="signal", level="info", symbol="BTC/USDT",
                             message="[LONG] BTC/USDT — EMA9 crossed above EMA21"),
                JournalEntry(entry_type="trade", level="info", symbol="BTC/USDT",
                             message="PAPER | BUY 0.015384 BTC/USDT @ 65000.00"),
                JournalEntry(entry_type="risk_block", level="warning", symbol="ETH/USDT",
                             message="BLOCKED: Position size $1200 exceeds max $1000"),
            ]
            session.add_all(entries)
            print("Created sample journal entries.")

        await session.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
