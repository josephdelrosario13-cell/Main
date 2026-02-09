"""
Backtesting framework for the SPX 0DTE strategy.

Replays historical data through the strategy engine to evaluate
performance, win rate, and risk metrics before going live.
"""

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

from spx_bot.config import AppConfig, config
from spx_bot.brokers.paper_broker import PaperMarketData, PaperOrderExecutor
from spx_bot.position_manager import PositionManager
from spx_bot.risk_manager import RiskManager
from spx_bot.strategies.credit_spreads import CreditSpreadStrategy

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class BacktestResult:
    """Aggregated backtest statistics."""
    start_date: date
    end_date: date
    trading_days: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_daily_pnl: float = 0.0
    min_daily_pnl: float = 0.0
    peak_equity: float = 0.0
    max_drawdown: float = 0.0
    daily_pnls: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def avg_daily_pnl(self) -> float:
        if not self.daily_pnls:
            return 0.0
        return np.mean(self.daily_pnls)

    @property
    def sharpe_ratio(self) -> float:
        if not self.daily_pnls or np.std(self.daily_pnls) == 0:
            return 0.0
        return (np.mean(self.daily_pnls) / np.std(self.daily_pnls)) * np.sqrt(252)

    @property
    def profit_factor(self) -> float:
        gains = sum(p for p in self.daily_pnls if p > 0)
        losses = abs(sum(p for p in self.daily_pnls if p < 0))
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    def print_report(self):
        print("\n" + "=" * 60)
        print("BACKTEST REPORT")
        print("=" * 60)
        print(f"Period:          {self.start_date} to {self.end_date}")
        print(f"Trading days:    {self.trading_days}")
        print(f"Total trades:    {self.total_trades}")
        print(f"Win rate:        {self.win_rate:.1%}")
        print(f"Total P&L:       ${self.total_pnl:,.2f}")
        print(f"Avg daily P&L:   ${self.avg_daily_pnl:,.2f}")
        print(f"Best day:        ${self.max_daily_pnl:,.2f}")
        print(f"Worst day:       ${self.min_daily_pnl:,.2f}")
        print(f"Max drawdown:    ${self.max_drawdown:,.2f}")
        print(f"Sharpe ratio:    {self.sharpe_ratio:.2f}")
        print(f"Profit factor:   {self.profit_factor:.2f}")
        print("=" * 60)


class Backtester:
    """
    Simulate the trading strategy over multiple days.

    Uses the paper broker with randomized but statistically
    representative price movements for each simulated day.
    """

    def __init__(self, cfg: AppConfig = config):
        self.cfg = cfg
        self.strategy = CreditSpreadStrategy(cfg, backtest_mode=True)

    def run(
        self,
        num_days: int = 20,
        base_price: float = 5800.0,
        base_vix: float = 18.0,
    ) -> BacktestResult:
        """
        Run a backtest over the specified number of trading days.

        Args:
            num_days: Number of trading days to simulate.
            base_price: Starting SPX price.
            base_vix: Starting VIX level.
        """
        result = BacktestResult(
            start_date=date.today() - timedelta(days=num_days),
            end_date=date.today(),
        )

        equity = self.cfg.account.starting_capital
        peak_equity = equity

        logger.info("Starting backtest: %d days from SPX %.0f, VIX %.1f",
                     num_days, base_price, base_vix)

        for day in range(num_days):
            # Simulate a new day with slight drift
            daily_drift = np.random.normal(0, base_price * 0.005)
            day_price = base_price + daily_drift
            day_vix = base_vix + np.random.normal(0, 1.5)
            day_vix = max(10, min(40, day_vix))

            daily_pnl = self._simulate_day(day_price, day_vix)

            equity += daily_pnl
            peak_equity = max(peak_equity, equity)
            drawdown = peak_equity - equity
            result.max_drawdown = max(result.max_drawdown, drawdown)
            result.peak_equity = peak_equity

            result.daily_pnls.append(daily_pnl)
            result.trading_days += 1

            if daily_pnl > result.max_daily_pnl:
                result.max_daily_pnl = daily_pnl
            if daily_pnl < result.min_daily_pnl:
                result.min_daily_pnl = daily_pnl

            # Update base_price for next day (random walk)
            base_price = day_price

        result.total_pnl = sum(result.daily_pnls)
        result.total_trades = sum(1 for p in result.daily_pnls if p != 0)
        result.winning_trades = sum(1 for p in result.daily_pnls if p > 0)
        result.losing_trades = sum(1 for p in result.daily_pnls if p < 0)

        return result

    def _simulate_day(self, base_price: float, base_vix: float) -> float:
        """Simulate a single trading day and return the P&L."""
        market_data = PaperMarketData(base_price=base_price, base_vix=base_vix)
        executor = PaperOrderExecutor(market_data)
        risk_mgr = RiskManager(self.cfg)
        position_mgr = PositionManager(
            executor=executor,
            market_data=market_data,
            risk_manager=risk_mgr,
            cfg=self.cfg,
        )

        market_data.connect()

        # Simulate multiple scan opportunities during the day
        num_scans = 12  # ~every 30 min from 9:45 to 14:30

        for scan in range(num_scans):
            can_trade, reason = risk_mgr.can_open_trade()
            if not can_trade:
                break

            # Simulate price movement between scans
            market_data._simulate_price_move()

            snapshot = market_data.get_spx_snapshot()
            chain = market_data.get_options_chain()

            max_size = risk_mgr.max_position_size()
            if max_size <= 0:
                break

            signals = self.strategy.scan_for_signals(
                chain=chain,
                snapshot=snapshot,
                max_position_size=max_size,
                contracts_calculator=risk_mgr.calculate_contracts,
            )

            if signals:
                best = signals[0]
                if best.confidence >= 0.5 and best.contracts > 0:
                    # Cap exposure
                    while (best.contracts > 0 and
                           best.total_max_loss > self.cfg.account.max_per_trade):
                        best.contracts -= 1

                    if best.contracts > 0:
                        order = best.to_order()
                        fill = executor.submit_order(order)
                        position_mgr.open_position(order, fill, snapshot.price)

            # Monitor and potentially exit existing positions
            if position_mgr.open_positions:
                # Simulate some price movement and check exits
                for _ in range(3):
                    market_data._simulate_price_move()
                position_mgr.monitor_positions()

        # End of day: close remaining positions
        if position_mgr.open_positions:
            position_mgr.close_all_positions("EOD")

        summary = position_mgr.get_summary()
        return summary["total_realized_pnl"]

    def export_results(self, result: BacktestResult, filepath: str = "data/backtest_results.csv"):
        """Export backtest results to CSV."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["day", "daily_pnl", "cumulative_pnl", "equity"])
            cumulative = 0
            equity = self.cfg.account.starting_capital
            for i, pnl in enumerate(result.daily_pnls, 1):
                cumulative += pnl
                equity += pnl
                writer.writerow([i, round(pnl, 2), round(cumulative, 2), round(equity, 2)])

        logger.info("Backtest results exported to %s", filepath)
