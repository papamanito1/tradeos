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

        # ── Demo strategies ────────────────────────────────────────────────────
        demo_strategies = [
            {
                "name": "BTC EMA Crossover",
                "strategy_type": "ema_crossover",
                "symbols": ["BTC/USDT"],
                "timeframe": "1h",
                "parameters": {"fast_period": 9, "slow_period": 21},
                "capital_allocation": 2000.0,
                "mode": "paper",
                "is_enabled": True,
            },
            {
                "name": "ETH Breakout",
                "strategy_type": "breakout",
                "symbols": ["ETH/USDT"],
                "timeframe": "4h",
                "parameters": {"lookback": 20},
                "capital_allocation": 1500.0,
                "mode": "paper",
                "is_enabled": True,
            },
            {
                "name": "SOL Mean Reversion",
                "strategy_type": "mean_reversion",
                "symbols": ["SOL/USDT"],
                "timeframe": "15m",
                "parameters": {"period": 20, "std_dev": 2.0},
                "capital_allocation": 1000.0,
                "mode": "off",
                "is_enabled": False,
            },
            {
                "name": "QOFS — BTC/ETH HFT Scalper",
                "strategy_type": "quantum_order_flow_scalper",
                "symbols": ["BTC/USDT", "ETH/USDT"],
                "timeframe": "1m",
                "parameters": {
                    "vwap_period": 50,
                    "vwap_entry_band_pct": 0.003,
                    "vwap_max_band_pct": 0.015,
                    "ema_fast": 3,
                    "ema_slow": 8,
                    "ofi_period": 10,
                    "ofi_threshold": 0.20,
                    "vol_period": 20,
                    "vol_min_pct": 0.0003,
                    "vol_max_pct": 0.025,
                    "rvol_period": 20,
                    "rvol_threshold": 1.25,
                    "regime_period": 30,
                    "regime_trend_thresh": 0.10,
                    "regime_revert_thresh": -0.10,
                    "min_score": 3,
                    "sl_pct": 0.003,
                    "tp_pct": 0.005,
                },
                "capital_allocation": 2500.0,
                "mode": "paper",
                "is_enabled": True,
            },
        ]

        for s_data in demo_strategies:
            result = await session.execute(select(Strategy).where(Strategy.name == s_data["name"]))
            if not result.scalar_one_or_none():
                session.add(Strategy(**s_data))
                print(f"Created strategy: {s_data['name']}")

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
