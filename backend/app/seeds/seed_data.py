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

        # ── Keep only our three live strategies ───────────────────────────────
        keep_names = {"BTC Momentum Velocity 15m", "HFT VWAP Scalper 1m", "ORB-30 Scalper 1m"}
        all_strats = await session.execute(select(Strategy))
        for s in all_strats.scalars().all():
            if s.name not in keep_names:
                await session.delete(s)
                print(f"Removed strategy: {s.name}")

        # ── Ensure BTC Momentum Velocity 15m exists and is enabled ────────────
        mv_name = "BTC Momentum Velocity 15m"
        result = await session.execute(select(Strategy).where(Strategy.name == mv_name))
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
                name=mv_name,
                strategy_type="btc_momentum_velocity",
                symbols=["BTC/USDT"],
                timeframe="15m",
                parameters=mv_params,
                capital_allocation=10000.0,
                mode="live",
                is_enabled=True,
            ))
            print(f"Created strategy: {mv_name}")
        else:
            existing_mv.is_enabled = True
            existing_mv.mode = "live"
            existing_mv.capital_allocation = 10000.0
            existing_mv.parameters = mv_params
            print(f"Updated strategy: {mv_name}")

        # ── Ensure HFT VWAP Scalper 1m exists and is enabled ─────────────────
        hft_name = "HFT VWAP Scalper 1m"
        result = await session.execute(select(Strategy).where(Strategy.name == hft_name))
        existing_hft = result.scalar_one_or_none()
        hft_params = {
            "obi_threshold":      0.18,
            "tfi_threshold":      0.12,
            "micro_edge_ticks":   0.15,
            "near_vwap_atr_mult": 0.20,
            "max_spread_ticks":   2,
            "sl_atr_mult":        0.35,
            "tp1_r":              0.6,
            "tp2_r":              1.2,
            "ema9_period":        9,
            "ema21_period":       21,
            "atr_period":         14,
            "swing_lookback":     10,
            "cooldown_bars":      3,
        }
        if not existing_hft:
            session.add(Strategy(
                name=hft_name,
                strategy_type="hft_vwap_scalper",
                symbols=["BTC/USDT"],
                timeframe="1m",
                parameters=hft_params,
                capital_allocation=5000.0,
                mode="live",
                is_enabled=True,
            ))
            print(f"Created strategy: {hft_name}")
        else:
            existing_hft.is_enabled = True
            existing_hft.mode = "live"
            existing_hft.capital_allocation = 5000.0
            existing_hft.parameters = hft_params
            print(f"Updated strategy: {hft_name}")

        # ── Ensure ORB-30 Scalper 1m exists and is enabled ───────────────────
        orb_name = "ORB-30 Scalper 1m"
        result = await session.execute(select(Strategy).where(Strategy.name == orb_name))
        existing_orb = result.scalar_one_or_none()
        orb_params = {
            "orb_bars":        30,
            "ema_period":      20,
            "rr_target":       2.25,
            "sl_buffer_pct":   0.0003,
            "vol_ratio":       1.3,
            "max_hold_bars":   90,
            "or_range_min":    0.08,
            "or_range_max":    3.5,
        }
        if not existing_orb:
            session.add(Strategy(
                name=orb_name,
                strategy_type="orb_scalper",
                symbols=["BTC/USDT"],
                timeframe="1m",
                parameters=orb_params,
                capital_allocation=5000.0,
                mode="live",
                is_enabled=True,
            ))
            print(f"Created strategy: {orb_name}")
        else:
            existing_orb.is_enabled = True
            existing_orb.mode = "live"
            existing_orb.capital_allocation = 5000.0
            existing_orb.parameters = orb_params
            print(f"Updated strategy: {orb_name}")

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
