"""
Logging setup and trade logger for CSV export.
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

from spx_bot.config import LoggingConfig


def setup_logging(log_cfg: LoggingConfig):
    """Configure application-wide logging."""
    log_dir = Path(log_cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=getattr(logging, log_cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file),
        ],
    )

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


class TradeLogger:
    """Logs trades to CSV files for analysis."""

    TRADE_HEADERS = [
        "timestamp", "position_id", "action", "strategy", "spread_side",
        "short_strike", "long_strike", "quantity", "price", "pnl",
        "reason", "spx_price", "confidence",
    ]

    DAILY_HEADERS = [
        "date", "trades_taken", "total_pnl", "peak_pnl",
        "max_drawdown_hit", "target_hit",
    ]

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_headers()

    def _ensure_headers(self):
        """Create CSV files with headers if they don't exist."""
        trade_file = self.log_dir / "trades.csv"
        if not trade_file.exists():
            with open(trade_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self.TRADE_HEADERS)

        daily_file = self.log_dir / "daily_summary.csv"
        if not daily_file.exists():
            with open(daily_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self.DAILY_HEADERS)

    def log_open(self, position, signal):
        """Log a trade open."""
        trade_file = self.log_dir / "trades.csv"
        with open(trade_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                position.position_id,
                "OPEN",
                signal.strategy.value,
                position.spread_side.value,
                position.short_strike,
                position.long_strike,
                position.quantity,
                position.entry_credit,
                "",  # no P&L on open
                signal.reason,
                position.underlying_at_entry,
                signal.confidence,
            ])

    def log_close(self, action: dict):
        """Log a trade close."""
        trade_file = self.log_dir / "trades.csv"
        with open(trade_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                action["position_id"],
                "CLOSE",
                "",  # strategy already logged on open
                "",
                "",
                "",
                action["quantity"],
                action["exit_debit"],
                action["pnl"],
                action["reason"],
                "",
                "",
            ])

    def log_daily_summary(self, summary: dict, risk_status: dict):
        """Log end-of-day summary."""
        daily_file = self.log_dir / "daily_summary.csv"
        with open(daily_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                risk_status["date"],
                risk_status["trades_taken"],
                summary["total_realized_pnl"],
                risk_status["peak_pnl"],
                risk_status["max_drawdown_hit"],
                risk_status["target_hit"],
            ])
