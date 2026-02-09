"""
Market data and options chain module.

Provides a unified interface to fetch:
- SPX price data (real-time and intraday bars)
- SPX options chains (0DTE)
- VIX level
- Greeks (delta, gamma, theta, vega)

Broker-specific implementations are in spx_bot/brokers/.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, date
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class OptionType(Enum):
    CALL = "call"
    PUT = "put"


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float  # implied volatility


@dataclass
class OptionQuote:
    """A single option contract quote."""
    symbol: str
    underlying_price: float
    strike: float
    option_type: OptionType
    expiration: date
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    greeks: Optional[Greeks] = None

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 2)

    @property
    def spread_pct(self) -> float:
        if self.mid == 0:
            return float("inf")
        return (self.ask - self.bid) / self.mid

    @property
    def dte(self) -> int:
        return (self.expiration - date.today()).days


@dataclass
class SpxSnapshot:
    """Current state of SPX and related data."""
    price: float
    vix: float
    timestamp: datetime
    daily_open: float
    daily_high: float
    daily_low: float
    prev_close: float

    @property
    def intraday_range(self) -> float:
        return self.daily_high - self.daily_low

    @property
    def change_from_open(self) -> float:
        return self.price - self.daily_open

    @property
    def change_from_open_pct(self) -> float:
        if self.daily_open == 0:
            return 0.0
        return (self.change_from_open / self.daily_open) * 100


@dataclass
class OptionsChain:
    """Full 0DTE options chain for SPX."""
    underlying_price: float
    expiration: date
    calls: list[OptionQuote]
    puts: list[OptionQuote]
    timestamp: datetime

    def get_strike_range(self, width: float) -> tuple[float, float]:
        """Return strikes within `width` points of current price."""
        low = self.underlying_price - width
        high = self.underlying_price + width
        return low, high

    def find_by_delta(
        self, option_type: OptionType, target_delta: float, tolerance: float = 0.03
    ) -> Optional[OptionQuote]:
        """Find the option closest to a target delta."""
        options = self.calls if option_type == OptionType.CALL else self.puts

        best = None
        best_diff = float("inf")

        for opt in options:
            if opt.greeks is None:
                continue
            diff = abs(opt.greeks.delta - target_delta)
            if diff < best_diff:
                best_diff = diff
                best = opt

        if best and best_diff <= tolerance:
            return best

        # Fallback: return closest even outside tolerance
        if best:
            logger.warning(
                "No option within delta tolerance %.2f. Best: delta=%.3f (diff=%.3f)",
                tolerance,
                best.greeks.delta if best.greeks else 0,
                best_diff,
            )
            return best

        return None

    def find_by_strike(
        self, option_type: OptionType, strike: float
    ) -> Optional[OptionQuote]:
        """Find option at exact strike."""
        options = self.calls if option_type == OptionType.CALL else self.puts
        for opt in options:
            if opt.strike == strike:
                return opt
        return None


class MarketDataProvider(ABC):
    """Abstract interface for market data. Implemented per broker."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to data source."""
        ...

    @abstractmethod
    def disconnect(self):
        """Clean up connections."""
        ...

    @abstractmethod
    def get_spx_snapshot(self) -> SpxSnapshot:
        """Get current SPX price and related data."""
        ...

    @abstractmethod
    def get_options_chain(self, expiration: Optional[date] = None) -> OptionsChain:
        """
        Fetch the SPX options chain.
        If expiration is None, fetch today's 0DTE chain.
        """
        ...

    @abstractmethod
    def get_vix(self) -> float:
        """Get current VIX level."""
        ...

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open for trading."""
        ...
