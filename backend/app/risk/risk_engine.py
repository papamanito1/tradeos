"""
Risk Engine — server-side enforcement of all risk rules.
This is the single authority: no trade executes without passing here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.core.redis_client import redis_get, redis_set

DAILY_PNL_KEY = "risk:daily_pnl"
CONSECUTIVE_LOSS_KEY = "risk:consecutive_losses:{strategy_id}"
COOLDOWN_KEY = "risk:cooldown:{strategy_id}"


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str
    rule: Optional[str] = None


@dataclass
class RiskConfig:
    max_daily_loss_usd: float = 50.0
    max_daily_loss_pct: float = 20.0
    max_position_size_usd: float = 50.0
    max_position_size_pct: float = 100.0
    max_leverage: float = 60.0
    max_open_trades: int = 5
    max_symbol_exposure_pct: float = 20.0
    cooldown_after_losses: int = 3
    cooldown_minutes: int = 60
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold_pct: float = 10.0
    symbol_blacklist: list[str] = None
    kill_switch_active: bool = False

    def __post_init__(self):
        if self.symbol_blacklist is None:
            self.symbol_blacklist = []


class RiskEngine:
    """
    All checks run on the backend — never rely on frontend validation alone.
    """

    def __init__(self, config: RiskConfig):
        self.config = config

    async def check(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        leverage: float,
        strategy_id: Optional[int],
        current_balance_usd: float,
        open_trade_count: int,
        current_symbol_exposure_usd: float,
    ) -> RiskCheckResult:

        # ── 0. Kill switch ────────────────────────────────────────────────────
        if self.config.kill_switch_active:
            return RiskCheckResult(False, "Kill switch is active — all trading halted", "kill_switch")

        # ── 1. Symbol blacklist ───────────────────────────────────────────────
        base = symbol.split("/")[0]
        if symbol in self.config.symbol_blacklist or base in self.config.symbol_blacklist:
            return RiskCheckResult(False, f"{symbol} is blacklisted", "symbol_blacklist")

        # ── 2. Leverage cap ───────────────────────────────────────────────────
        if leverage > self.config.max_leverage:
            return RiskCheckResult(
                False,
                f"Leverage {leverage}x exceeds max {self.config.max_leverage}x",
                "max_leverage",
            )

        # ── 3. Max open trades ────────────────────────────────────────────────
        if open_trade_count >= self.config.max_open_trades:
            return RiskCheckResult(
                False,
                f"Open trades ({open_trade_count}) at maximum ({self.config.max_open_trades})",
                "max_open_trades",
            )

        # ── 4. Position size (absolute) ───────────────────────────────────────
        notional = amount * price
        if notional > self.config.max_position_size_usd:
            return RiskCheckResult(
                False,
                f"Position size ${notional:.2f} exceeds max ${self.config.max_position_size_usd:.2f}",
                "max_position_size_usd",
            )

        # ── 5. Position size (% of balance) ───────────────────────────────────
        if current_balance_usd > 0:
            size_pct = notional / current_balance_usd * 100
            if size_pct > self.config.max_position_size_pct:
                return RiskCheckResult(
                    False,
                    f"Position {size_pct:.1f}% of balance exceeds max {self.config.max_position_size_pct:.1f}%",
                    "max_position_size_pct",
                )

        # ── 6. Symbol exposure cap ────────────────────────────────────────────
        new_exposure = current_symbol_exposure_usd + notional
        if current_balance_usd > 0:
            exposure_pct = new_exposure / current_balance_usd * 100
            if exposure_pct > self.config.max_symbol_exposure_pct:
                return RiskCheckResult(
                    False,
                    f"Symbol exposure {exposure_pct:.1f}% exceeds max {self.config.max_symbol_exposure_pct:.1f}%",
                    "symbol_exposure",
                )

        # ── 7. Daily loss limit ────────────────────────────────────────────────
        daily_pnl = await self._get_daily_pnl()
        if daily_pnl < -self.config.max_daily_loss_usd:
            return RiskCheckResult(
                False,
                f"Daily loss ${abs(daily_pnl):.2f} exceeds max ${self.config.max_daily_loss_usd:.2f}",
                "max_daily_loss_usd",
            )
        if current_balance_usd > 0:
            daily_pnl_pct = daily_pnl / current_balance_usd * 100
            if daily_pnl_pct < -self.config.max_daily_loss_pct:
                return RiskCheckResult(
                    False,
                    f"Daily loss {abs(daily_pnl_pct):.1f}% exceeds max {self.config.max_daily_loss_pct:.1f}%",
                    "max_daily_loss_pct",
                )

        # ── 8. Circuit breaker ────────────────────────────────────────────────
        if self.config.circuit_breaker_enabled and current_balance_usd > 0:
            drawdown_pct = abs(daily_pnl) / current_balance_usd * 100
            if drawdown_pct >= self.config.circuit_breaker_threshold_pct:
                return RiskCheckResult(
                    False,
                    f"Circuit breaker triggered: drawdown {drawdown_pct:.1f}%",
                    "circuit_breaker",
                )

        # ── 9. Cooldown after consecutive losses ──────────────────────────────
        if strategy_id is not None:
            cooldown_until = await redis_get(COOLDOWN_KEY.format(strategy_id=strategy_id))
            if cooldown_until:
                try:
                    until = datetime.fromisoformat(cooldown_until)
                    if datetime.now(timezone.utc) < until:
                        remaining = int((until - datetime.now(timezone.utc)).total_seconds() / 60)
                        return RiskCheckResult(
                            False,
                            f"Strategy in cooldown for {remaining}m after consecutive losses",
                            "cooldown",
                        )
                except Exception:
                    pass

        return RiskCheckResult(True, "All risk checks passed")

    async def record_trade_result(self, strategy_id: int, pnl: float) -> None:
        """Update daily PnL and consecutive loss tracker."""
        daily_pnl = await self._get_daily_pnl()
        await redis_set(DAILY_PNL_KEY, daily_pnl + pnl, ex=86400)

        if pnl < 0:
            key = CONSECUTIVE_LOSS_KEY.format(strategy_id=strategy_id)
            losses = await redis_get(key) or 0
            losses += 1
            await redis_set(key, losses, ex=86400)
            if losses >= self.config.cooldown_after_losses:
                cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=self.config.cooldown_minutes)
                await redis_set(
                    COOLDOWN_KEY.format(strategy_id=strategy_id),
                    cooldown_until.isoformat(),
                    ex=self.config.cooldown_minutes * 60,
                )
        else:
            # reset on winning trade
            await redis_set(CONSECUTIVE_LOSS_KEY.format(strategy_id=strategy_id), 0, ex=86400)

    async def _get_daily_pnl(self) -> float:
        val = await redis_get(DAILY_PNL_KEY)
        return float(val) if val is not None else 0.0

    async def get_daily_pnl(self) -> float:
        return await self._get_daily_pnl()


# Module-level singleton factory (loaded with DB settings at startup)
_risk_engine: Optional[RiskEngine] = None


def get_risk_engine() -> RiskEngine:
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine(RiskConfig())
    return _risk_engine


def update_risk_engine(config: RiskConfig) -> None:
    global _risk_engine
    _risk_engine = RiskEngine(config)
