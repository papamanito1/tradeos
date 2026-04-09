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
                "name": "Smart Money — BTC Liquidity Sweep",
                "strategy_type": "smart_money_sweep",
                "symbols": ["BTC/USDT"],
                "timeframe": "1m",
                "parameters": {
                    "htf_ema": 200,
                    "htf_slope_bars": 50,
                    "htf_min_slope": 0.0002,
                    "atr_period": 14,
                    "atr_min_pct": 0.0010,
                    "atr_max_pct": 0.0500,
                    "vwap_period": 200,
                    "vwap_band_pct": 0.004,
                    "pullback_ema": 21,
                    "pullback_ema_band": 0.003,
                    "sweep_lookback": 10,
                    "sweep_min_pct": 0.0003,
                    "reclaim_min_pct": 0.0005,
                    "funding_lookback": 30,
                    "funding_max_bias": 0.80,
                    "news_vol_period": 20,
                    "news_vol_spike": 3.0,
                    "atr_sl_mult": 1.5,
                    "r_mult_tp1": 1.0,
                    "r_mult_tp2": 2.0,
                },
                "capital_allocation": 3000.0,
                "mode": "paper",
                "is_enabled": True,
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
