"""Tests for the credit spread strategy engine."""

import pytest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from spx_bot.config import AppConfig, StrategyConfig
from spx_bot.market_data import (
    Greeks, OptionQuote, OptionType, OptionsChain, SpxSnapshot,
)
from spx_bot.strategies.credit_spreads import CreditSpreadStrategy

ET = ZoneInfo("America/New_York")


def _make_chain(price=5800.0, short_put_strike=5770.0, short_call_strike=5830.0):
    """Helper to create a test options chain."""
    puts = []
    calls = []

    for offset in range(-10, 11):
        strike = round(price + offset * 5, 0)

        put_delta = -0.5 + (price - strike) / price * 5
        put_delta = max(-0.99, min(-0.01, put_delta))
        put_price = max(0.10, abs(put_delta) * 10)

        call_delta = 0.5 - (strike - price) / price * 5
        call_delta = max(0.01, min(0.99, call_delta))
        call_price = max(0.10, call_delta * 10)

        puts.append(OptionQuote(
            symbol=f"SPX_P{strike:.0f}",
            underlying_price=price,
            strike=strike,
            option_type=OptionType.PUT,
            expiration=date.today(),
            bid=round(put_price - 0.05, 2),
            ask=round(put_price + 0.05, 2),
            last=round(put_price, 2),
            volume=1000,
            open_interest=5000,
            greeks=Greeks(delta=put_delta, gamma=0.01, theta=-0.5, vega=0.1, iv=0.18),
        ))

        calls.append(OptionQuote(
            symbol=f"SPX_C{strike:.0f}",
            underlying_price=price,
            strike=strike,
            option_type=OptionType.CALL,
            expiration=date.today(),
            bid=round(call_price - 0.05, 2),
            ask=round(call_price + 0.05, 2),
            last=round(call_price, 2),
            volume=1000,
            open_interest=5000,
            greeks=Greeks(delta=call_delta, gamma=0.01, theta=-0.5, vega=0.1, iv=0.18),
        ))

    return OptionsChain(
        underlying_price=price,
        expiration=date.today(),
        calls=calls,
        puts=puts,
        timestamp=datetime.now(ET),
    )


def _make_snapshot(price=5800.0, vix=18.0):
    return SpxSnapshot(
        price=price,
        vix=vix,
        timestamp=datetime.now(ET),
        daily_open=price - 2,
        daily_high=price + 10,
        daily_low=price - 10,
        prev_close=price - 5,
    )


@pytest.fixture
def strategy():
    return CreditSpreadStrategy()


def test_vix_filter_too_low(strategy):
    """Should return no signals when VIX is below minimum."""
    chain = _make_chain()
    snapshot = _make_snapshot(vix=8.0)  # Below min_vix

    with patch("spx_bot.strategies.credit_spreads.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2024, 1, 15, 10, 30, tzinfo=ET)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        signals = strategy.scan_for_signals(
            chain=chain,
            snapshot=snapshot,
            max_position_size=1200,
            contracts_calculator=lambda x: max(1, int(1200 / x)),
        )

    assert len(signals) == 0


def test_bias_assessment(strategy):
    """Test market bias detection."""
    bullish = _make_snapshot(price=5810)
    bullish.daily_open = 5790  # Big up move
    assert strategy._assess_bias(bullish) == "bullish"

    bearish = _make_snapshot(price=5780)
    bearish.daily_open = 5810  # Big down move
    assert strategy._assess_bias(bearish) == "bearish"


def test_score_setup(strategy):
    """Test confidence scoring."""
    score = strategy._score_setup(
        credit=2.0,
        spread_width=5,
        delta=0.16,
        vix=20,
        bias="bullish",
        side="put",
    )
    assert 0.0 <= score <= 1.0
    assert score >= 0.7  # Should be high confidence
