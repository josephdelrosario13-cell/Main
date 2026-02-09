"""
Paper trading broker implementation.

Simulates order execution and market data for testing and development.
Uses randomized but realistic SPX options data.
"""

import logging
import random
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

from spx_bot.market_data import (
    Greeks,
    MarketDataProvider,
    OptionQuote,
    OptionType,
    OptionsChain,
    SpxSnapshot,
)
from spx_bot.orders import (
    Fill,
    Order,
    OrderExecutor,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class PaperMarketData(MarketDataProvider):
    """Simulated market data for paper trading."""

    def __init__(self, base_price: float = 5800.0, base_vix: float = 18.0):
        self._base_price = base_price
        self._base_vix = base_vix
        self._price = base_price
        self._vix = base_vix
        self._daily_open = base_price
        self._daily_high = base_price
        self._daily_low = base_price
        self._prev_close = base_price - random.uniform(-20, 20)
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        logger.info("Paper market data connected. Base SPX: %.2f", self._base_price)
        return True

    def disconnect(self):
        self._connected = False

    def get_spx_snapshot(self) -> SpxSnapshot:
        self._simulate_price_move()
        return SpxSnapshot(
            price=self._price,
            vix=self._vix,
            timestamp=datetime.now(ET),
            daily_open=self._daily_open,
            daily_high=self._daily_high,
            daily_low=self._daily_low,
            prev_close=self._prev_close,
        )

    def get_options_chain(self, expiration: Optional[date] = None) -> OptionsChain:
        exp = expiration or date.today()
        self._simulate_price_move()

        calls = []
        puts = []

        # Generate strikes every 5 points, ±100 from current price
        center = round(self._price / 5) * 5
        strikes = [center + i * 5 for i in range(-20, 21)]

        for strike in strikes:
            moneyness = (strike - self._price) / self._price
            dte_fraction = max(0.001, 1 / 365)  # 0DTE

            # Simplified Black-Scholes-ish pricing
            iv = (self._vix / 100) * (1 + abs(moneyness) * 2)  # skew

            call_price, put_price, call_delta, put_delta = self._price_option(
                self._price, strike, iv, dte_fraction
            )

            call_greeks = Greeks(
                delta=call_delta,
                gamma=self._approx_gamma(self._price, strike, iv, dte_fraction),
                theta=-call_price * 0.8,  # aggressive 0DTE theta
                vega=self._approx_vega(self._price, strike, iv, dte_fraction),
                iv=iv,
            )
            put_greeks = Greeks(
                delta=put_delta,
                gamma=call_greeks.gamma,
                theta=-put_price * 0.8,
                vega=call_greeks.vega,
                iv=iv,
            )

            bid_ask_spread = max(0.10, call_price * 0.05)

            calls.append(OptionQuote(
                symbol=f"SPX{exp.strftime('%y%m%d')}C{strike:.0f}",
                underlying_price=self._price,
                strike=strike,
                option_type=OptionType.CALL,
                expiration=exp,
                bid=round(max(0.05, call_price - bid_ask_spread / 2), 2),
                ask=round(call_price + bid_ask_spread / 2, 2),
                last=round(call_price, 2),
                volume=random.randint(100, 5000),
                open_interest=random.randint(500, 20000),
                greeks=call_greeks,
            ))

            puts.append(OptionQuote(
                symbol=f"SPX{exp.strftime('%y%m%d')}P{strike:.0f}",
                underlying_price=self._price,
                strike=strike,
                option_type=OptionType.PUT,
                expiration=exp,
                bid=round(max(0.05, put_price - bid_ask_spread / 2), 2),
                ask=round(put_price + bid_ask_spread / 2, 2),
                last=round(put_price, 2),
                volume=random.randint(100, 5000),
                open_interest=random.randint(500, 20000),
                greeks=put_greeks,
            ))

        return OptionsChain(
            underlying_price=self._price,
            expiration=exp,
            calls=calls,
            puts=puts,
            timestamp=datetime.now(ET),
        )

    def get_vix(self) -> float:
        self._vix += random.gauss(0, 0.2)
        self._vix = max(10, min(50, self._vix))
        return round(self._vix, 2)

    def is_market_open(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:  # Saturday/Sunday
            return False
        market_open = time(9, 30)
        market_close = time(16, 0)
        return market_open <= now.time() <= market_close

    def _simulate_price_move(self):
        """Random walk the price slightly."""
        move = random.gauss(0, self._price * 0.0003)
        self._price += move
        self._price = round(self._price, 2)
        self._daily_high = max(self._daily_high, self._price)
        self._daily_low = min(self._daily_low, self._price)
        return self._price

    @staticmethod
    def _price_option(
        spot: float, strike: float, iv: float, dte_fraction: float
    ) -> tuple[float, float, float, float]:
        """Simplified option pricing returning (call_price, put_price, call_delta, put_delta)."""
        from scipy.stats import norm

        if dte_fraction <= 0:
            call_intrinsic = max(0, spot - strike)
            put_intrinsic = max(0, strike - spot)
            call_delta = 1.0 if spot > strike else 0.0
            put_delta = -1.0 if spot < strike else 0.0
            return call_intrinsic, put_intrinsic, call_delta, put_delta

        sqrt_t = np.sqrt(dte_fraction)
        d1 = (np.log(spot / strike) + (0.05 + iv**2 / 2) * dte_fraction) / (iv * sqrt_t)
        d2 = d1 - iv * sqrt_t

        call_price = spot * norm.cdf(d1) - strike * np.exp(-0.05 * dte_fraction) * norm.cdf(d2)
        put_price = strike * np.exp(-0.05 * dte_fraction) * norm.cdf(-d2) - spot * norm.cdf(-d1)

        call_delta = round(norm.cdf(d1), 4)
        put_delta = round(norm.cdf(d1) - 1, 4)

        return (
            round(max(0.05, call_price), 2),
            round(max(0.05, put_price), 2),
            call_delta,
            put_delta,
        )

    @staticmethod
    def _approx_gamma(spot, strike, iv, dte_fraction):
        from scipy.stats import norm

        sqrt_t = max(0.001, np.sqrt(dte_fraction))
        d1 = (np.log(spot / strike) + (0.05 + iv**2 / 2) * dte_fraction) / (iv * sqrt_t)
        return round(norm.pdf(d1) / (spot * iv * sqrt_t), 6)

    @staticmethod
    def _approx_vega(spot, strike, iv, dte_fraction):
        from scipy.stats import norm

        sqrt_t = max(0.001, np.sqrt(dte_fraction))
        d1 = (np.log(spot / strike) + (0.05 + iv**2 / 2) * dte_fraction) / (iv * sqrt_t)
        return round(spot * norm.pdf(d1) * sqrt_t / 100, 4)


class PaperOrderExecutor(OrderExecutor):
    """Simulated order execution with realistic fills."""

    def __init__(self, market_data: PaperMarketData):
        self.market_data = market_data
        self._order_id_counter = 0

    def submit_order(self, order: Order) -> Fill:
        self._order_id_counter += 1
        order.order_id = f"PAPER-{self._order_id_counter}"

        # Simulate fill at mid price with small slippage
        slippage = random.uniform(0, 0.05)

        if order.order_type == OrderType.CREDIT:
            fill_price = round(order.limit_price - slippage, 2)
        else:
            fill_price = round(order.limit_price + slippage, 2)

        fill = Fill(
            order_id=order.order_id,
            fill_price=fill_price,
            quantity=order.quantity,
            timestamp=datetime.now(ET),
            status=OrderStatus.FILLED,
        )

        logger.info(
            "Paper fill: %s %d @ $%.2f (requested $%.2f)",
            order.order_id,
            order.quantity,
            fill_price,
            order.limit_price,
        )
        return fill

    def cancel_order(self, order_id: str) -> bool:
        logger.info("Paper cancel: %s", order_id)
        return True

    def get_order_status(self, order_id: str) -> OrderStatus:
        return OrderStatus.FILLED
