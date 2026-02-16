"""Data models for trade tracking."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class AssetClass(Enum):
    STOCK = "STK"
    OPTION = "OPT"
    FUTURE = "FUT"
    FOREX = "CASH"
    OTHER = "OTHER"


@dataclass
class Execution:
    """A single execution/fill from IBKR."""
    exec_id: str
    order_id: int
    account: str
    symbol: str
    side: Side
    quantity: float
    price: float
    timestamp: datetime
    asset_class: AssetClass = AssetClass.STOCK
    exchange: str = ""
    commission: float = 0.0
    realized_pnl: Optional[float] = None


@dataclass
class Trade:
    """A completed round-trip trade (open + close)."""
    trade_id: str
    symbol: str
    asset_class: AssetClass
    side: Side  # side of the opening leg
    open_time: datetime
    close_time: datetime
    open_price: float
    close_price: float
    quantity: float
    commission: float
    pnl: float  # net P&L after commissions
    account: str = ""

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def is_loser(self) -> bool:
        return self.pnl < 0

    @property
    def trade_date(self) -> date:
        return self.close_time.date()

    @property
    def hold_duration_seconds(self) -> float:
        return (self.close_time - self.open_time).total_seconds()


@dataclass
class DailyStats:
    """Aggregated stats for a single trading day."""
    date: date
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    scratch: int = 0  # breakeven trades
    gross_pnl: float = 0.0
    total_commissions: float = 0.0
    net_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    trades: list = field(default_factory=list)
