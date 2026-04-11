"""
PaperTrader — Virtual $10K Paper Trading Engine
=================================================
Runs 24/7 alongside the live agent. Takes every qualified signal
with realistic position sizing, tracks a virtual balance, and feeds
results into MasterBrain for continuous learning.

Persistence: full state saved to SQLite on every trade close.
Balance, positions, and trade history survive restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

import os as _os
# Prefer a persistent data directory (Railway volume mount or explicit env var).
# Falls back to /tmp only if nothing else is configured — this is volatile on restarts.
PAPER_DB_PATH = _os.environ.get(
    "PAPER_DB_PATH",
    _os.path.join(_os.environ.get("DATA_DIR", "/tmp"), "paper_trader.db"),
)
STARTING_BALANCE = 10_000.0

try:
    import aiosqlite
    _SQLITE = True
except ImportError:
    _SQLITE = False


class PaperTrader:
    """
    Virtual trading engine with its own $10K balance.
    Separate from the live executor — never touches BingX.
    """

    RISK_PER_TRADE_PCT = 0.02    # 2% of balance per trade
    MAX_OPEN_POSITIONS = 10
    MAX_LEVERAGE       = 20      # conservative paper leverage
    DEFAULT_LEVERAGE   = 10

    def __init__(self) -> None:
        self.balance:        float = STARTING_BALANCE
        self.starting_balance: float = STARTING_BALANCE
        self.positions:      dict[str, dict] = {}
        self.trades:         list[dict] = []
        self.stats:          dict = self._empty_stats()
        self._db_ready:      bool = False
        self._daily_pnl:     float = 0.0
        self._day_str:       str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _empty_stats(self) -> dict:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
            "peak_balance": STARTING_BALANCE, "max_drawdown_pct": 0.0,
        }

    def _check_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day_str:
            self._day_str = today
            self._daily_pnl = 0.0

    # ── Position sizing ──────────────────────────────────────────────────────

    def _calc_size(self, leverage: int) -> float:
        """Returns the margin (collateral) to allocate per trade.
        The notional exposure is margin * leverage, handled by the caller."""
        margin = self.balance * self.RISK_PER_TRADE_PCT
        return round(margin, 2)

    # ── Open position ────────────────────────────────────────────────────────

    def open_position(
        self,
        strategy_key: str,
        strategy_name: str,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        confidence: float,
        leverage: int = 0,
        reasoning: str = "",
    ) -> Optional[dict]:
        paper_key = f"paper_{strategy_key}"

        if self.positions.get(paper_key):
            return None

        if len([v for v in self.positions.values() if v]) >= self.MAX_OPEN_POSITIONS:
            return None

        if self.balance < 10.0:
            return None

        lev = min(leverage or self.DEFAULT_LEVERAGE, self.MAX_LEVERAGE)
        margin = self._calc_size(lev)
        if margin > self.balance:
            margin = self.balance * 0.5
        notional = margin * lev
        btc_size = notional / entry_price if entry_price > 0 else 0

        rr = 0.0
        sl_dist = abs(entry_price - sl_price) if sl_price else 0
        tp_dist = abs(tp_price - entry_price) if tp_price else 0
        if sl_dist > 0:
            rr = tp_dist / sl_dist

        pos = {
            "id":             f"paper-{strategy_key}-{int(time.time()*1000)}",
            "strategy_key":   paper_key,
            "strategy_name":  f"[PAPER] {strategy_name}",
            "strat_key_ref":  strategy_key,
            "direction":      direction,
            "entry":          round(entry_price, 2),
            "sl":             round(sl_price, 2) if sl_price else 0,
            "tp":             round(tp_price, 2) if tp_price else 0,
            "margin_usdc":    round(margin, 2),
            "leverage":       lev,
            "btc_size":       round(btc_size, 6),
            "confidence":     confidence,
            "reasoning":      reasoning[:200],
            "rr":             f"1:{rr:.1f}",
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "current_price":  entry_price,
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
            "mode":           "paper_trader",
        }
        self.positions[paper_key] = pos
        logger.info(f"[PaperTrader] OPENED {direction.upper()} {strategy_name} @ ${entry_price:.0f} "
                    f"· margin ${margin:.2f} · {lev}x · bal ${self.balance:.2f}")
        return pos

    # ── Update prices ────────────────────────────────────────────────────────

    def update_prices(self, live_price: float) -> None:
        for key, pos in list(self.positions.items()):
            if not pos:
                continue
            entry = pos["entry"]
            d = pos["direction"]
            diff = (live_price - entry) if d == "long" else (entry - live_price)
            pnl = round(diff * pos["btc_size"] * pos["leverage"], 2)
            pct = round(diff / entry * 100, 4) if entry > 0 else 0
            self.positions[key] = {
                **pos,
                "current_price": live_price,
                "unrealized_pnl": pnl,
                "unrealized_pct": pct,
            }

    # ── Check SL/TP ──────────────────────────────────────────────────────────

    def check_sl_tp(self, live_price: float) -> list[dict]:
        """Check all positions for SL/TP hits. Returns list of closed trades."""
        closed = []
        for key, pos in list(self.positions.items()):
            if not pos:
                continue
            d  = pos["direction"]
            sl = pos.get("sl") or 0
            tp = pos.get("tp") or 0

            hit_tp = (d == "long" and tp and live_price >= tp) or (d == "short" and tp and live_price <= tp)
            hit_sl = (d == "long" and sl and live_price <= sl) or (d == "short" and sl and live_price >= sl)

            if hit_tp or hit_sl:
                reason = "tp" if hit_tp else "sl"
                exit_price = tp if hit_tp else sl
                trade = self._close(key, exit_price, reason)
                if trade:
                    closed.append(trade)

            # Timeout: close after 4 hours
            try:
                opened = datetime.fromisoformat(pos["timestamp"].replace("Z", "+00:00"))
                age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
                if age_min > 240:
                    trade = self._close(key, live_price, "timeout")
                    if trade:
                        closed.append(trade)
            except Exception:
                pass

        return closed

    # ── Close position ───────────────────────────────────────────────────────

    def _close(self, key: str, exit_price: float, reason: str) -> Optional[dict]:
        pos = self.positions.get(key)
        if not pos:
            return None

        entry = pos["entry"]
        d     = pos["direction"]
        diff  = (exit_price - entry) if d == "long" else (entry - exit_price)
        pnl   = round(diff * pos["btc_size"] * pos["leverage"], 2)
        pct   = round(diff / entry * 100, 4) if entry > 0 else 0

        self.balance = round(self.balance + pnl, 2)
        self._check_day()
        self._daily_pnl = round(self._daily_pnl + pnl, 2)

        trade = {
            **pos,
            "exit_price":  exit_price,
            "exit_reason": reason,
            "pnl_usd":     pnl,
            "pnl_pct":     pct,
            "closed_at":   datetime.now(timezone.utc).isoformat(),
            "balance_after": self.balance,
        }
        self.trades.insert(0, trade)
        if len(self.trades) > 1000:
            self.trades = self.trades[:1000]

        # Update stats
        s = self.stats
        s["total_trades"] += 1
        if pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1
        s["total_pnl"]    = round(s["total_pnl"] + pnl, 2)
        s["win_rate"]      = round(s["wins"] / s["total_trades"] * 100, 1) if s["total_trades"] > 0 else 0.0
        s["best_trade"]    = max(s["best_trade"], pnl)
        s["worst_trade"]   = min(s["worst_trade"], pnl)
        s["peak_balance"]  = max(s.get("peak_balance", STARTING_BALANCE), self.balance)
        if s["peak_balance"] > 0:
            dd = (s["peak_balance"] - self.balance) / s["peak_balance"] * 100
            s["max_drawdown_pct"] = max(s.get("max_drawdown_pct", 0), dd)

        self.positions[key] = None
        logger.info(f"[PaperTrader] CLOSED {pos['strategy_name']} {reason.upper()} @ ${exit_price:.0f} "
                    f"· P&L {'+' if pnl>=0 else ''}${pnl:.2f} · bal ${self.balance:.2f}")

        asyncio.get_event_loop().create_task(self._save_to_db()) if _SQLITE else None
        return trade

    # ── Status for API ───────────────────────────────────────────────────────

    def get_status(self) -> dict:
        self._check_day()
        open_positions = [p for p in self.positions.values() if p]
        return_pct = ((self.balance - self.starting_balance) / self.starting_balance * 100) if self.starting_balance > 0 else 0

        equity_curve = []
        running_bal = self.starting_balance
        for t in reversed(self.trades[-50:]):
            running_bal = t.get("balance_after", running_bal)
            equity_curve.append({
                "balance":   running_bal,
                "pnl":       t.get("pnl_usd", 0),
                "strategy":  t.get("strat_key_ref", ""),
                "reason":    t.get("exit_reason", ""),
                "closed_at": t.get("closed_at", ""),
            })

        return {
            "enabled":           True,
            "balance":           round(self.balance, 2),
            "starting_balance":  self.starting_balance,
            "return_pct":        round(return_pct, 2),
            "daily_pnl":         round(self._daily_pnl, 2),
            "open_positions":    open_positions,
            "open_count":        len(open_positions),
            "stats":             self.stats,
            "recent_trades":     self.trades[:20],
            "equity_curve":      equity_curve,
        }

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "balance":          self.balance,
            "starting_balance": self.starting_balance,
            "positions":        self.positions,
            "trades":           self.trades[-500:],
            "stats":            self.stats,
            "daily_pnl":        self._daily_pnl,
            "day_str":          self._day_str,
        }

    def from_dict(self, data: dict) -> None:
        self.balance          = data.get("balance", STARTING_BALANCE)
        self.starting_balance = data.get("starting_balance", STARTING_BALANCE)
        self.positions        = data.get("positions", {})
        self.trades           = data.get("trades", [])[-500:]
        self.stats            = data.get("stats", self._empty_stats())
        self._daily_pnl       = data.get("daily_pnl", 0.0)
        self._day_str         = data.get("day_str", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        # Rebuild stats from trade history to ensure accuracy
        self._rebuild_stats()
        logger.info(f"[PaperTrader] Restored: balance=${self.balance:.2f} · "
                    f"{self.stats['total_trades']} trades · "
                    f"P&L ${self.stats['total_pnl']:.2f}")

    def _rebuild_stats(self) -> None:
        s = self._empty_stats()
        bal = self.starting_balance
        for t in reversed(self.trades):
            pnl = t.get("pnl_usd", 0) or 0
            s["total_trades"] += 1
            if pnl > 0:
                s["wins"] += 1
            else:
                s["losses"] += 1
            s["total_pnl"]   = round(s["total_pnl"] + pnl, 2)
            s["best_trade"]  = max(s["best_trade"], pnl)
            s["worst_trade"] = min(s["worst_trade"], pnl)
            bal += pnl
            s["peak_balance"] = max(s["peak_balance"], bal)
            if s["peak_balance"] > 0:
                dd = (s["peak_balance"] - bal) / s["peak_balance"] * 100
                s["max_drawdown_pct"] = max(s["max_drawdown_pct"], dd)
        s["win_rate"] = round(s["wins"] / s["total_trades"] * 100, 1) if s["total_trades"] > 0 else 0.0
        self.stats = s

    # ── SQLite persistence ───────────────────────────────────────────────────

    async def _ensure_db(self) -> None:
        if self._db_ready or not _SQLITE:
            return
        try:
            async with aiosqlite.connect(PAPER_DB_PATH) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS paper_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        state TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                await db.commit()
            self._db_ready = True
        except Exception as e:
            logger.debug(f"[PaperTrader] DB init error: {e}")

    async def _save_to_db(self) -> None:
        if not _SQLITE:
            return
        await self._ensure_db()
        try:
            payload = json.dumps(self.to_dict())
            ts = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(PAPER_DB_PATH) as db:
                await db.execute(
                    "INSERT INTO paper_state (id, state, updated_at) VALUES (1, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET state = ?, updated_at = ?",
                    (payload, ts, payload, ts)
                )
                await db.commit()
        except Exception as e:
            logger.debug(f"[PaperTrader] DB save error: {e}")

    async def load_from_db(self) -> bool:
        if not _SQLITE:
            return False
        await self._ensure_db()
        try:
            async with aiosqlite.connect(PAPER_DB_PATH) as db:
                async with db.execute("SELECT state FROM paper_state WHERE id = 1") as cursor:
                    row = await cursor.fetchone()
            if row:
                data = json.loads(row[0])
                self.from_dict(data)
                return True
        except Exception as e:
            logger.debug(f"[PaperTrader] DB load error: {e}")
        return False
