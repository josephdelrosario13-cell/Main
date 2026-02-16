"""Performance analytics engine.

Computes daily, weekly, monthly, and YTD statistics from trade data.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from trade_tracker.database import TradeDatabase
from trade_tracker.models import DailyStats, Trade

ET = ZoneInfo("America/New_York")


class PerformanceAnalytics:
    """Compute performance metrics over various time periods."""

    def __init__(self, db: TradeDatabase):
        self.db = db

    # ------------------------------------------------------------------
    # Core stat computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_stats(trades: list[Trade], period_date: date) -> DailyStats:
        """Compute aggregate stats from a list of trades."""
        stats = DailyStats(date=period_date)
        if not trades:
            return stats

        stats.total_trades = len(trades)
        stats.trades = trades

        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl < 0]
        scratch = [t for t in trades if t.pnl == 0]

        stats.winners = len(winners)
        stats.losers = len(losers)
        stats.scratch = len(scratch)

        stats.gross_pnl = sum(t.pnl + t.commission for t in trades)
        stats.total_commissions = sum(t.commission for t in trades)
        stats.net_pnl = sum(t.pnl for t in trades)

        if winners:
            stats.avg_win = sum(t.pnl for t in winners) / len(winners)
            stats.largest_win = max(t.pnl for t in winners)
        if losers:
            stats.avg_loss = sum(t.pnl for t in losers) / len(losers)
            stats.largest_loss = min(t.pnl for t in losers)

        if stats.total_trades > 0:
            stats.win_rate = stats.winners / stats.total_trades

        total_wins = sum(t.pnl for t in winners)
        total_losses = abs(sum(t.pnl for t in losers))
        if total_losses > 0:
            stats.profit_factor = total_wins / total_losses

        return stats

    # ------------------------------------------------------------------
    # Daily stats
    # ------------------------------------------------------------------

    def daily_stats(self, target_date: date) -> DailyStats:
        """Get stats for a specific day."""
        trades = self.db.get_trades(start_date=target_date, end_date=target_date)
        return self.compute_stats(trades, target_date)

    def daily_stats_range(
        self, start: date, end: date,
    ) -> list[DailyStats]:
        """Get daily stats for each trading day in a range."""
        trades = self.db.get_trades(start_date=start, end_date=end)

        by_day: dict[date, list[Trade]] = defaultdict(list)
        for t in trades:
            by_day[t.trade_date].append(t)

        results = []
        current = start
        while current <= end:
            if current in by_day:
                results.append(self.compute_stats(by_day[current], current))
            current += timedelta(days=1)

        return results

    # ------------------------------------------------------------------
    # Weekly stats
    # ------------------------------------------------------------------

    def weekly_stats(self, week_start: Optional[date] = None) -> DailyStats:
        """Stats for a given week (Mon-Fri). Defaults to current week."""
        if week_start is None:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())

        week_end = week_start + timedelta(days=4)  # Friday
        trades = self.db.get_trades(start_date=week_start, end_date=week_end)
        return self.compute_stats(trades, week_start)

    def weekly_stats_range(
        self, num_weeks: int = 12,
    ) -> list[DailyStats]:
        """Get weekly stats for the past N weeks."""
        today = date.today()
        current_monday = today - timedelta(days=today.weekday())

        results = []
        for i in range(num_weeks):
            monday = current_monday - timedelta(weeks=i)
            stats = self.weekly_stats(monday)
            if stats.total_trades > 0:
                results.append(stats)

        results.reverse()
        return results

    # ------------------------------------------------------------------
    # Monthly stats
    # ------------------------------------------------------------------

    def monthly_stats(self, year: int, month: int) -> DailyStats:
        """Stats for a given month."""
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)

        trades = self.db.get_trades(start_date=start, end_date=end)
        return self.compute_stats(trades, start)

    def monthly_stats_range(
        self, num_months: int = 12,
    ) -> list[DailyStats]:
        """Get monthly stats for the past N months."""
        today = date.today()
        results = []

        for i in range(num_months):
            month = today.month - i
            year = today.year
            while month <= 0:
                month += 12
                year -= 1
            stats = self.monthly_stats(year, month)
            if stats.total_trades > 0:
                results.append(stats)

        results.reverse()
        return results

    # ------------------------------------------------------------------
    # YTD stats
    # ------------------------------------------------------------------

    def ytd_stats(self, year: Optional[int] = None) -> DailyStats:
        """Year-to-date stats."""
        if year is None:
            year = date.today().year

        start = date(year, 1, 1)
        end = date.today()
        trades = self.db.get_trades(start_date=start, end_date=end)
        return self.compute_stats(trades, start)

    # ------------------------------------------------------------------
    # Cumulative P&L curve
    # ------------------------------------------------------------------

    def cumulative_pnl(
        self, start: Optional[date] = None, end: Optional[date] = None,
    ) -> list[tuple[date, float]]:
        """Return (date, cumulative_pnl) pairs for charting equity curve."""
        trades = self.db.get_trades(start_date=start, end_date=end)

        by_day: dict[date, float] = defaultdict(float)
        for t in trades:
            by_day[t.trade_date] += t.pnl

        if not by_day:
            return []

        sorted_dates = sorted(by_day.keys())
        cumulative = []
        running = 0.0
        for d in sorted_dates:
            running += by_day[d]
            cumulative.append((d, round(running, 2)))

        return cumulative

    # ------------------------------------------------------------------
    # Summary report
    # ------------------------------------------------------------------

    def print_summary(self, stats: DailyStats, label: str = ""):
        """Print a formatted summary to stdout."""
        header = f" {label} " if label else " Performance Summary "
        print(f"\n{'=' * 50}")
        print(f"{header:=^50}")
        print(f"{'=' * 50}")
        print(f"  Total trades:     {stats.total_trades}")
        print(f"  Winners:          {stats.winners}  "
              f"({stats.win_rate:.1%})")
        print(f"  Losers:           {stats.losers}")
        print(f"  Scratch:          {stats.scratch}")
        print(f"  Net P&L:          ${stats.net_pnl:,.2f}")
        print(f"  Gross P&L:        ${stats.gross_pnl:,.2f}")
        print(f"  Commissions:      ${stats.total_commissions:,.2f}")
        print(f"  Avg win:          ${stats.avg_win:,.2f}")
        print(f"  Avg loss:         ${stats.avg_loss:,.2f}")
        print(f"  Largest win:      ${stats.largest_win:,.2f}")
        print(f"  Largest loss:     ${stats.largest_loss:,.2f}")
        print(f"  Profit factor:    {stats.profit_factor:.2f}")
        print(f"{'=' * 50}\n")
