"""
Production-grade Backtesting Engine
=====================================

Execution model  (no lookahead bias):
  Bar i close → generate signal → entry at bar i+1 open price (± slippage)

Intra-bar SL/TP:
  For each bar while in a position, uses bar.high and bar.low to detect
  whether SL or TP was crossed.  If both were crossed, the order of execution
  is estimated by proximity to bar.open.

Costs:
  commission_pct  – applied on entry notional AND exit proceeds (round-trip)
  slippage_pct    – moves entry/exit price against the position (adverse fill)

Position sizing:
  Percentage of realized equity (compounding).
"""
from __future__ import annotations

import math
import statistics
from calendar import month_abbr
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.exchange.base import Candle
from app.strategies import STRATEGY_REGISTRY, SignalDirection

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# ── Constants ─────────────────────────────────────────────────────────────────
BARS_PER_YEAR: dict[str, float] = {
    "1m": 525_600, "3m": 175_200, "5m": 105_120, "15m": 35_040,
    "30m": 17_520, "1h": 8_760, "2h": 4_380, "4h": 2_190,
    "6h": 1_460, "8h": 1_095, "12h": 730, "1d": 365, "1w": 52,
}
TF_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
    "8h": 28800, "12h": 43200, "1d": 86400, "1w": 604800,
}
MAX_BARS = 2000


# ── Request / Response models ─────────────────────────────────────────────────
class BacktestRequest(BaseModel):
    strategy_type: str
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    limit: int = Field(default=500, ge=50, le=MAX_BARS)
    initial_capital: float = Field(default=10000.0, gt=0)
    position_size_pct: float = Field(default=10.0, gt=0, le=100)
    commission_pct: float = Field(default=0.1, ge=0)   # 0.1% = 10 bps per leg
    slippage_pct: float = Field(default=0.05, ge=0)    # 0.05% adverse fill
    parameters: dict[str, Any] = {}


# ── OHLCV fetcher (real Binance data, paginated) ──────────────────────────────
async def _fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> list[Candle]:
    """
    Fetch real historical OHLCV from Binance public REST API.
    Uses httpx directly to avoid CCXT's exchange-info preflight request.
    Paginates backwards to fulfil requests > 1000 bars.
    No API key required.
    """
    try:
        import httpx
        # Binance uses "BTCUSDT" format, not "BTC/USDT"
        binance_symbol = symbol.replace("/", "")
        tf_ms = TF_SECONDS.get(timeframe, 3600) * 1000
        base_url = "https://api.binance.com/api/v3/klines"

        all_raw: list = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            # Fetch most recent bars first
            params = {"symbol": binance_symbol, "interval": timeframe, "limit": min(limit, 1000)}
            resp = await client.get(base_url, params=params)
            resp.raise_for_status()
            raw1 = resp.json()
            all_raw = list(raw1)

            # Paginate backwards for more bars
            while len(all_raw) < limit and len(raw1) == 1000:
                oldest_ts = all_raw[0][0]
                since = oldest_ts - tf_ms * min(limit - len(all_raw), 1000)
                params2 = {
                    "symbol": binance_symbol, "interval": timeframe,
                    "limit": min(limit - len(all_raw), 1000),
                    "startTime": since, "endTime": oldest_ts - 1,
                }
                resp2 = await client.get(base_url, params=params2)
                resp2.raise_for_status()
                raw2 = resp2.json()
                if not raw2:
                    break
                existing_ts = {r[0] for r in all_raw}
                prepend = [r for r in raw2 if r[0] not in existing_ts]
                all_raw = prepend + all_raw
                raw1 = raw2

        # Sort by timestamp asc, trim to requested limit
        all_raw.sort(key=lambda r: r[0])
        all_raw = all_raw[-limit:]

        return [
            Candle(
                timestamp=datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
                open=float(c[1]),
                high=float(c[2]),
                low=float(c[3]),
                close=float(c[4]),
                volume=float(c[5]),
            )
            for c in all_raw
        ]
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch market data from Binance: {type(e).__name__}: {e}",
        )


# ── Position tracker ──────────────────────────────────────────────────────────
class _Position:
    __slots__ = ("side", "entry_price", "entry_bar", "entry_time", "size", "sl", "tp", "entry_fee")

    def __init__(
        self,
        side: str,
        entry_price: float,
        entry_bar: int,
        entry_time: datetime,
        size: float,
        sl: Optional[float],
        tp: Optional[float],
        entry_fee: float,
    ):
        self.side = side
        self.entry_price = entry_price
        self.entry_bar = entry_bar
        self.entry_time = entry_time
        self.size = size
        self.sl = sl
        self.tp = tp
        self.entry_fee = entry_fee

    def unrealized(self, price: float) -> float:
        """Mark-to-market unrealized PnL at given price."""
        if self.side == "long":
            return (price - self.entry_price) * self.size
        return (self.entry_price - price) * self.size

    def check_sl_tp(self, bar: Candle) -> tuple[Optional[float], Optional[str]]:
        """
        Intra-bar SL/TP check.
        Returns (exit_price, reason) or (None, None).
        If both are crossed in the same bar, the one closer to bar.open fires first.
        """
        sl, tp = self.sl, self.tp
        sl_hit = tp_hit = False

        if self.side == "long":
            if sl and bar.low <= sl:
                sl_hit = True
            if tp and bar.high >= tp:
                tp_hit = True
        else:
            if sl and bar.high >= sl:
                sl_hit = True
            if tp and bar.low <= tp:
                tp_hit = True

        if sl_hit and tp_hit:
            # Whichever SL/TP is closer to the open fires first
            sl_dist = abs(bar.open - sl)
            tp_dist = abs(bar.open - tp)
            return (sl, "stop_loss") if sl_dist <= tp_dist else (tp, "take_profit")
        if sl_hit:
            return sl, "stop_loss"
        if tp_hit:
            return tp, "take_profit"
        return None, None


# ── Metric computation ────────────────────────────────────────────────────────
def _compute_metrics(
    equity_values: list[float],
    trades: list[dict],
    initial_capital: float,
    final_capital: float,
    timeframe: str,
    total_bars: int,
) -> dict:
    bpy = BARS_PER_YEAR.get(timeframe, 365)

    # Per-bar returns for Sharpe/Sortino
    bar_returns = [
        equity_values[i] / equity_values[i - 1] - 1
        for i in range(1, len(equity_values))
        if equity_values[i - 1] > 0
    ]

    # Total & annualised return
    total_return_pct = (final_capital / initial_capital - 1) * 100
    n = len(equity_values)
    if n > 1 and initial_capital > 0:
        annualized_return_pct = ((final_capital / initial_capital) ** (bpy / n) - 1) * 100
    else:
        annualized_return_pct = 0.0

    # Sharpe (excess return / vol, rf=0)
    sharpe = 0.0
    if len(bar_returns) >= 2:
        std = statistics.stdev(bar_returns)
        if std > 0:
            sharpe = statistics.mean(bar_returns) / std * math.sqrt(bpy)

    # Sortino (only downside deviation)
    sortino = 0.0
    downside = [r for r in bar_returns if r < 0]
    if downside and len(bar_returns) >= 2:
        if len(downside) >= 2:
            dsd = statistics.stdev(downside)
        else:
            dsd = abs(downside[0])
        if dsd > 0:
            sortino = statistics.mean(bar_returns) / dsd * math.sqrt(bpy)

    # Max drawdown (from equity peak)
    peak = initial_capital
    max_dd_pct = 0.0
    max_dd_usd = 0.0
    max_dd_dur = 0
    cur_dd_start = 0
    drawdown_series: list[float] = []

    for i, eq in enumerate(equity_values):
        if eq > peak:
            peak = eq
            cur_dd_start = i
        dd_pct = (peak - eq) / peak * 100 if peak > 0 else 0.0
        drawdown_series.append(round(dd_pct, 3))
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usd = peak - eq
            max_dd_dur = i - cur_dd_start

    # Calmar
    calmar = annualized_return_pct / max_dd_pct if max_dd_pct > 0 else 0.0

    # Trade-level stats
    total = len(trades)
    winning = [t for t in trades if t["pnl"] > 0]
    losing = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in winning)
    gross_loss = abs(sum(t["pnl"] for t in losing))

    win_rate = len(winning) / total * 100 if total else 0.0
    avg_win = gross_profit / len(winning) if winning else 0.0
    avg_loss = sum(t["pnl"] for t in losing) / len(losing) if losing else 0.0
    largest_win = max((t["pnl"] for t in winning), default=0.0)
    largest_loss = min((t["pnl"] for t in losing), default=0.0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )
    expectancy = sum(t["pnl"] for t in trades) / total if total else 0.0
    avg_dur = sum(t["duration_bars"] for t in trades) / total if total else 0.0
    total_fees = sum(t.get("fees", 0.0) for t in trades)

    # Consecutive wins/losses
    max_cw = max_cl = cw = cl = 0
    for t in trades:
        if t["pnl"] > 0:
            cw += 1; cl = 0
            max_cw = max(max_cw, cw)
        else:
            cl += 1; cw = 0
            max_cl = max(max_cl, cl)

    # Time-in-market
    bars_in_market = sum(t["duration_bars"] for t in trades)
    exposure_pct = bars_in_market / total_bars * 100 if total_bars else 0.0

    return {
        "total_return_pct": round(total_return_pct, 4),
        "annualized_return_pct": round(annualized_return_pct, 4),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "calmar_ratio": round(calmar, 3),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_duration_bars": max_dd_dur,
        "total_trades": total,
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
        "profit_factor": round(min(profit_factor, 999.0), 3),
        "expectancy": round(expectancy, 2),
        "avg_duration_bars": round(avg_dur, 1),
        "max_consecutive_wins": max_cw,
        "max_consecutive_losses": max_cl,
        "total_fees_paid": round(total_fees, 2),
        "exposure_pct": round(exposure_pct, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "drawdown_series": drawdown_series,
    }


def _monthly_returns(equity_curve: list[dict]) -> dict[str, float]:
    """
    Aggregate per-bar equity data into calendar-month returns.
    Returns { "2024-01": 3.24, "2024-02": -1.50, ... }
    """
    if not equity_curve:
        return {}

    monthly: dict[str, list[float]] = {}
    for point in equity_curve:
        ts = point["timestamp"][:7]  # "YYYY-MM"
        monthly.setdefault(ts, [])
        monthly[ts].append(point["equity"])

    result: dict[str, float] = {}
    for month, values in sorted(monthly.items()):
        start, end = values[0], values[-1]
        if start > 0:
            result[month] = round((end / start - 1) * 100, 2)
    return result


def _duration_human(bars: int, timeframe: str) -> str:
    secs = bars * TF_SECONDS.get(timeframe, 3600)
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


# ── Main backtest endpoint ─────────────────────────────────────────────────────
@router.post("/run")
async def run_backtest(
    req: BacktestRequest,
    current_user: dict = Depends(get_current_user),
):
    if req.strategy_type not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {req.strategy_type}")

    # ── 1. Fetch real OHLCV data ───────────────────────────────────────────────
    candles = await _fetch_ohlcv(req.symbol, req.timeframe, req.limit)
    if len(candles) < 60:
        raise HTTPException(status_code=422, detail="Not enough candle data returned from exchange")

    cls = STRATEGY_REGISTRY[req.strategy_type]
    params = {**cls.default_parameters, **req.parameters}
    strategy = cls(parameters=params)

    # Warm-up: use strategy's own _MIN_CANDLES if defined, else derive from params
    warm_up = getattr(cls, "_MIN_CANDLES", None)
    if warm_up is None:
        warm_up = max(
            params.get("slow_period", 0),
            params.get("slow_ema", 0),
            params.get("period", 0),
            26,
        ) + 5  # small buffer

    # ── 2. Event-driven simulation loop ───────────────────────────────────────
    commission = req.commission_pct / 100
    slippage = req.slippage_pct / 100

    cash = req.initial_capital      # realized equity (after all closed trades)
    position: Optional[_Position] = None
    pending: Optional[dict] = None  # signal from previous bar, to execute at current bar open

    equity_curve: list[dict] = []   # [{timestamp, equity}] per bar
    trades: list[dict] = []

    def _close(pos: _Position, exit_price: float, reason: str, bar_idx: int, bar_time: datetime) -> None:
        nonlocal cash
        # Apply slippage against the position (adverse fill)
        slip = slippage * (1 if pos.side == "long" else -1)
        fill_price = exit_price * (1 - slip if pos.side == "long" else 1 + slip)
        # Gross PnL
        raw_pnl = (fill_price - pos.entry_price) * pos.size
        if pos.side == "short":
            raw_pnl = -raw_pnl
        # Exit commission
        exit_fee = abs(pos.size * fill_price) * commission
        net_pnl = raw_pnl - exit_fee
        # Update realized cash
        cash += net_pnl
        dur = bar_idx - pos.entry_bar
        total_fees = pos.entry_fee + exit_fee
        trades.append({
            "id": len(trades) + 1,
            "side": pos.side,
            "entry_bar": pos.entry_bar,
            "exit_bar": bar_idx,
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": bar_time.isoformat(),
            "entry_price": round(pos.entry_price, 6),
            "exit_price": round(fill_price, 6),
            "size": round(pos.size, 8),
            "pnl": round(net_pnl, 4),
            "pnl_pct": round(net_pnl / (pos.size * pos.entry_price) * 100, 4) if pos.size > 0 else 0,
            "fees": round(total_fees, 4),
            "reason": reason,
            "duration_bars": dur,
            "duration_human": _duration_human(dur, req.timeframe),
        })

    for i, bar in enumerate(candles):
        # ── a. Execute pending signal from previous bar at this bar's OPEN ────
        if pending is not None:
            direction = pending["direction"]

            # If we're in an opposite position, close it first (reversal)
            if position is not None and position.side != direction:
                _close(position, bar.open, "signal_reversal", i, bar.timestamp)
                position = None

            # Open new position if flat
            if position is None:
                slip = slippage * (1 if direction == "long" else -1)
                entry_fill = bar.open * (1 + slip if direction == "long" else 1 - slip)
                pos_usd = cash * req.position_size_pct / 100
                entry_fee = pos_usd * commission
                size = pos_usd / entry_fill if entry_fill > 0 else 0
                if size > 0:
                    cash -= entry_fee  # pay entry commission immediately
                    position = _Position(
                        side=direction,
                        entry_price=entry_fill,
                        entry_bar=i,
                        entry_time=bar.timestamp,
                        size=size,
                        sl=pending.get("sl"),
                        tp=pending.get("tp"),
                        entry_fee=entry_fee,
                    )
            pending = None

        # ── b. Intra-bar SL/TP check (do NOT check on entry bar itself) ───────
        if position is not None and i > position.entry_bar:
            exit_price, reason = position.check_sl_tp(bar)
            if exit_price is not None:
                _close(position, exit_price, reason, i, bar.timestamp)
                position = None

        # ── c. Mark-to-market equity at bar close ─────────────────────────────
        if position is not None:
            mark_equity = cash + position.unrealized(bar.close)
        else:
            mark_equity = cash
        equity_curve.append({
            "timestamp": bar.timestamp.isoformat(),
            "equity": round(mark_equity, 4),
        })

        # ── d. Generate signal at bar CLOSE (for execution at NEXT bar open) ──
        if i >= warm_up and i < len(candles) - 1:  # -1: no signal on last bar
            sig = strategy.generate_signal(candles[: i + 1], req.symbol, req.timeframe)
            if sig.direction in (SignalDirection.long, SignalDirection.short):
                new_dir = sig.direction.value
                # Only queue if: flat, OR opposite to current position
                if position is None or position.side != new_dir:
                    pending = {
                        "direction": new_dir,
                        "sl": sig.suggested_sl,
                        "tp": sig.suggested_tp,
                    }

    # ── 3. Close any open position at last bar ────────────────────────────────
    if position is not None:
        last = candles[-1]
        _close(position, last.close, "end_of_data", len(candles) - 1, last.timestamp)

    final_capital = round(cash, 4)
    equity_values = [p["equity"] for p in equity_curve]

    # ── 4. Compute full metric suite ──────────────────────────────────────────
    metrics = _compute_metrics(
        equity_values, trades, req.initial_capital, final_capital,
        req.timeframe, len(candles)
    )
    monthly = _monthly_returns(equity_curve)

    return {
        "strategy_type": req.strategy_type,
        "strategy_name": cls.name,
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "parameters": params,
        "date_range": {
            "start": candles[0].timestamp.isoformat(),
            "end": candles[-1].timestamp.isoformat(),
        },
        "candle_count": len(candles),
        "initial_capital": req.initial_capital,
        "final_capital": final_capital,
        "metrics": metrics,
        "equity_curve": equity_curve,
        "monthly_returns": monthly,
        "trades": trades,
    }


@router.get("/strategy-params/{strategy_type}")
async def get_strategy_params(
    strategy_type: str,
    current_user: dict = Depends(get_current_user),
):
    if strategy_type not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=404, detail="Strategy not found")
    cls = STRATEGY_REGISTRY[strategy_type]
    return {
        "type": strategy_type,
        "name": cls.name,
        "description": cls.description,
        "parameters": cls.default_parameters,
    }
