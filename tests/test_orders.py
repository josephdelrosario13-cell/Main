"""Tests for orders and position tracking."""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from spx_bot.orders import Position, SpreadSide

ET = ZoneInfo("America/New_York")


def _make_position(**kwargs):
    defaults = dict(
        position_id="POS-0001",
        spread_side=SpreadSide.BULL_PUT,
        short_strike=5770.0,
        long_strike=5765.0,
        quantity=2,
        entry_credit=1.80,
        spread_width=5.0,
        entry_time=datetime.now(ET),
        underlying_at_entry=5800.0,
    )
    defaults.update(kwargs)
    return Position(**defaults)


def test_max_profit():
    pos = _make_position(entry_credit=1.80, quantity=2)
    # 1.80 * 100 * 2 = $360
    assert pos.max_profit == 360.0


def test_max_loss():
    pos = _make_position(entry_credit=1.80, spread_width=5.0, quantity=2)
    # (5.0 - 1.80) * 100 * 2 = $640
    assert pos.max_loss == 640.0


def test_breakeven_bull_put():
    pos = _make_position(
        spread_side=SpreadSide.BULL_PUT,
        short_strike=5770.0,
        entry_credit=1.80,
    )
    assert pos.breakeven == 5768.2


def test_breakeven_bear_call():
    pos = _make_position(
        spread_side=SpreadSide.BEAR_CALL,
        short_strike=5830.0,
        entry_credit=1.50,
    )
    assert pos.breakeven == 5831.5


def test_unrealized_pnl():
    pos = _make_position(entry_credit=1.80, quantity=2)
    # If we can buy back at 0.90, profit = (1.80 - 0.90) * 100 * 2 = $180
    assert pos.unrealized_pnl(0.90) == 180.0
    # If spread moved against us to 3.00
    assert pos.unrealized_pnl(3.00) == -240.0


def test_profit_target():
    pos = _make_position(entry_credit=1.80, quantity=2)
    # Max profit = $360. At 50% target = $180
    # Current mid 0.90 -> unrealized = $180 -> exactly 50%
    assert pos.should_take_profit(0.90, 50) is True
    assert pos.should_take_profit(1.20, 50) is False


def test_stop_loss():
    pos = _make_position(entry_credit=1.80, quantity=2)
    # Stop at 2x credit loss = 1.80 * 100 * 2 * 2 = $720 loss
    # Current mid 5.40 -> unrealized = (1.80-5.40)*100*2 = -$720
    assert pos.should_stop_loss(5.40, 2.0) is True
    assert pos.should_stop_loss(3.00, 2.0) is False


def test_realized_pnl():
    pos = _make_position(entry_credit=1.80, quantity=2)
    pos.exit_credit = 0.50
    # (1.80 - 0.50) * 100 * 2 = $260
    assert pos.realized_pnl == 260.0
