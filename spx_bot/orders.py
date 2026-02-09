"""
Order management and execution interfaces.

Handles order construction, submission, and lifecycle tracking
for vertical spread trades on SPX options.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class OrderType(Enum):
    CREDIT = "credit"   # Selling spreads (collect premium)
    DEBIT = "debit"     # Buying spreads (pay premium)


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class SpreadSide(Enum):
    BULL_PUT = "bull_put"       # Sell higher put, buy lower put
    BEAR_CALL = "bear_call"     # Sell lower call, buy higher call


@dataclass
class SpreadLeg:
    """One leg of a vertical spread."""
    symbol: str
    strike: float
    action: str  # "BUY" or "SELL"
    quantity: int
    option_type: str  # "call" or "put"


@dataclass
class Order:
    """A complete spread order ready for submission."""
    spread_side: SpreadSide
    legs: list[SpreadLeg]
    quantity: int
    order_type: OrderType
    limit_price: float
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    @property
    def short_strike(self) -> float:
        for leg in self.legs:
            if leg.action == "SELL":
                return leg.strike
        return 0.0

    @property
    def long_strike(self) -> float:
        for leg in self.legs:
            if leg.action == "BUY":
                return leg.strike
        return 0.0

    @property
    def spread_width(self) -> float:
        return abs(self.short_strike - self.long_strike)


@dataclass
class Fill:
    """Execution fill details."""
    order_id: str
    fill_price: float
    quantity: int
    timestamp: datetime
    status: OrderStatus


@dataclass
class Position:
    """An open spread position being managed."""
    position_id: str
    spread_side: SpreadSide
    short_strike: float
    long_strike: float
    quantity: int
    entry_credit: float       # Credit received per spread
    spread_width: float       # Distance between strikes
    entry_time: datetime
    underlying_at_entry: float
    exit_credit: Optional[float] = None
    exit_time: Optional[datetime] = None
    is_open: bool = True

    @property
    def max_profit(self) -> float:
        """Maximum profit = credit received * 100 * quantity."""
        return self.entry_credit * 100 * self.quantity

    @property
    def max_loss(self) -> float:
        """Maximum loss = (spread_width - credit) * 100 * quantity."""
        return (self.spread_width - self.entry_credit) * 100 * self.quantity

    @property
    def realized_pnl(self) -> Optional[float]:
        """P&L when position is closed."""
        if self.exit_credit is None:
            return None
        # For credit spreads: profit = (entry_credit - exit_debit) * 100 * qty
        return (self.entry_credit - self.exit_credit) * 100 * self.quantity

    @property
    def breakeven(self) -> float:
        """Breakeven price for the short strike side."""
        if self.spread_side == SpreadSide.BULL_PUT:
            return self.short_strike - self.entry_credit
        else:
            return self.short_strike + self.entry_credit

    def unrealized_pnl(self, current_mid: float) -> float:
        """
        Estimate unrealized P&L given current mid price to close.

        Args:
            current_mid: Current mid price to buy back the spread.
        """
        return (self.entry_credit - current_mid) * 100 * self.quantity

    def should_take_profit(self, current_mid: float, target_pct: float) -> bool:
        """Check if position hit profit target."""
        pnl = self.unrealized_pnl(current_mid)
        return pnl >= self.max_profit * (target_pct / 100)

    def should_stop_loss(self, current_mid: float, stop_multiple: float) -> bool:
        """Check if position hit stop loss (loss > multiple of credit)."""
        pnl = self.unrealized_pnl(current_mid)
        return pnl <= -(self.entry_credit * 100 * self.quantity * stop_multiple)


class OrderExecutor(ABC):
    """Abstract interface for order execution."""

    @abstractmethod
    def submit_order(self, order: Order) -> Fill:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        ...
