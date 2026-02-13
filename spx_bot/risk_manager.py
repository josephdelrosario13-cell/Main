"""
Risk management engine.

Enforces all hard limits:
- Max 3 trades per day
- Max $200 per trade
- Max 20% daily drawdown ($200)
- Daily P&L target of 20% ($200)
- Position sizing based on remaining risk budget
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from spx_bot.config import AppConfig, config

logger = logging.getLogger(__name__)


@dataclass
class DailyRiskState:
    """Tracks intraday risk metrics. Reset at the start of each trading day."""
    trading_date: date = field(default_factory=date.today)
    trades_taken: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown_hit: bool = False
    target_hit: bool = False

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    def update_peak(self):
        if self.total_pnl > self.peak_pnl:
            self.peak_pnl = self.total_pnl


class RiskManager:
    """Evaluates whether new trades are permitted and sizes positions."""

    def __init__(self, cfg: AppConfig = config):
        self.cfg = cfg
        self.state = DailyRiskState()

    def reset_for_new_day(self):
        """Reset all daily counters. Called at market open."""
        logger.info(
            "Resetting risk state for new day. Previous day P&L: $%.2f",
            self.state.realized_pnl,
        )
        self.state = DailyRiskState()

    def record_trade_open(self):
        self.state.trades_taken += 1
        logger.info("Trade opened. Count today: %d", self.state.trades_taken)

    def record_trade_close(self, pnl: float):
        self.state.realized_pnl += pnl
        logger.info(
            "Trade closed. P&L: $%.2f | Daily realized: $%.2f",
            pnl,
            self.state.realized_pnl,
        )
        self._check_limits()

    def update_unrealized(self, unrealized: float):
        self.state.unrealized_pnl = unrealized
        self.state.update_peak()
        self._check_limits()

    def can_open_trade(self) -> tuple[bool, str]:
        """Check all risk gates before allowing a new trade."""

        if self.state.trading_date != date.today():
            self.reset_for_new_day()

        # Gate 1: Max trades per day
        if self.state.trades_taken >= self.cfg.account.max_trades_per_day:
            return False, f"Max trades reached ({self.cfg.account.max_trades_per_day})"

        # Gate 2: Daily drawdown limit
        if self.state.realized_pnl <= -self.cfg.account.max_daily_drawdown:
            self.state.max_drawdown_hit = True
            return False, (
                f"Max daily drawdown hit: ${self.state.realized_pnl:.2f} "
                f"(limit: -${self.cfg.account.max_daily_drawdown:.2f})"
            )

        # Gate 3: Intraday drawdown from peak
        drawdown_from_peak = self.state.peak_pnl - self.state.total_pnl
        if drawdown_from_peak >= self.cfg.account.max_daily_drawdown:
            self.state.max_drawdown_hit = True
            return False, (
                f"Drawdown from peak hit: ${drawdown_from_peak:.2f} "
                f"(limit: ${self.cfg.account.max_daily_drawdown:.2f})"
            )

        # Gate 4: Daily target already hit
        if self.state.realized_pnl >= self.cfg.account.daily_return_target:
            self.state.target_hit = True
            return False, (
                f"Daily target reached: ${self.state.realized_pnl:.2f} "
                f"(target: ${self.cfg.account.daily_return_target:.2f})"
            )

        return True, "OK"

    def max_position_size(self) -> float:
        """
        Calculate the maximum capital to allocate to the next trade.

        Uses the per-trade cap ($1,200) as the primary limit, reduced
        only if the remaining daily loss budget is lower.
        """
        remaining_loss_budget = self.cfg.account.max_daily_drawdown + self.state.realized_pnl
        if remaining_loss_budget <= 0:
            return 0.0

        trades_remaining = self.cfg.account.max_trades_per_day - self.state.trades_taken
        if trades_remaining <= 0:
            return 0.0

        # Per-trade cap is the primary limit; only reduce if remaining
        # loss budget can't cover even a single trade at the cap
        return min(remaining_loss_budget, self.cfg.account.max_per_trade)

    def calculate_contracts(self, max_loss_per_contract: float) -> int:
        """
        Determine how many spread contracts to trade.

        Args:
            max_loss_per_contract: Maximum loss per single contract
                                   (spread_width - credit) * 100
        """
        if max_loss_per_contract <= 0:
            return 0

        budget = self.max_position_size()
        contracts = int(budget / max_loss_per_contract)

        # At least 1 contract if we have budget, capped at what fits
        return max(0, contracts)

    def _check_limits(self):
        """Internal check after each P&L update."""
        if self.state.realized_pnl <= -self.cfg.account.max_daily_drawdown:
            self.state.max_drawdown_hit = True
            logger.warning("RISK LIMIT: Max daily drawdown breached!")

        if self.state.realized_pnl >= self.cfg.account.daily_return_target:
            self.state.target_hit = True
            logger.info("Daily profit target reached!")

    def get_status(self) -> dict:
        """Return current risk state as a dictionary for logging/display."""
        return {
            "date": str(self.state.trading_date),
            "trades_taken": self.state.trades_taken,
            "trades_remaining": self.cfg.account.max_trades_per_day - self.state.trades_taken,
            "realized_pnl": round(self.state.realized_pnl, 2),
            "unrealized_pnl": round(self.state.unrealized_pnl, 2),
            "total_pnl": round(self.state.total_pnl, 2),
            "peak_pnl": round(self.state.peak_pnl, 2),
            "max_position_size": round(self.max_position_size(), 2),
            "max_drawdown_hit": self.state.max_drawdown_hit,
            "target_hit": self.state.target_hit,
        }
