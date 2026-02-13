"""Tests for the risk management module."""

import pytest
from datetime import date
from spx_bot.config import AccountConfig, AppConfig
from spx_bot.risk_manager import RiskManager


@pytest.fixture
def risk_mgr():
    cfg = AppConfig(
        account=AccountConfig(
            starting_capital=1000,
            daily_return_target_pct=20,
            max_daily_drawdown_pct=20,
            max_trades_per_day=3,
            max_per_trade=200,
        )
    )
    return RiskManager(cfg)


def test_initial_state(risk_mgr):
    can, reason = risk_mgr.can_open_trade()
    assert can is True
    assert reason == "OK"


def test_max_trades_limit(risk_mgr):
    for _ in range(3):
        risk_mgr.record_trade_open()
    can, reason = risk_mgr.can_open_trade()
    assert can is False
    assert "Max trades" in reason


def test_drawdown_limit(risk_mgr):
    risk_mgr.record_trade_close(-100)
    risk_mgr.record_trade_close(-120)
    # Total loss: -220, exceeds -200 drawdown
    can, reason = risk_mgr.can_open_trade()
    assert can is False
    assert "drawdown" in reason.lower()


def test_target_hit(risk_mgr):
    risk_mgr.record_trade_close(120)
    risk_mgr.record_trade_close(100)
    # Total profit: 220, exceeds 200 target
    can, reason = risk_mgr.can_open_trade()
    assert can is False
    assert "target" in reason.lower()


def test_position_sizing(risk_mgr):
    size = risk_mgr.max_position_size()
    # $200 remaining budget, capped at $200 per trade -> $200
    assert size == 200.0

    # After a loss, budget shrinks
    risk_mgr.record_trade_close(-50)
    risk_mgr.record_trade_open()
    size = risk_mgr.max_position_size()
    # Remaining budget: $150, capped at $200 -> $150
    assert size == 150.0


def test_contract_calculation(risk_mgr):
    # Max loss per contract = $350 (e.g., 5-wide spread, $1.50 credit)
    # Budget = $200, $200 / $350 = 0 contracts (can't afford one)
    contracts = risk_mgr.calculate_contracts(350)
    assert contracts == 0

    # Smaller max loss per contract = $100
    # Budget = $200, $200 / $100 = 2 contracts
    contracts = risk_mgr.calculate_contracts(100)
    assert contracts == 2


def test_per_trade_cap(risk_mgr):
    # Simulate 2 trades with profit
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_close(80)
    risk_mgr.record_trade_close(80)

    # With $360 budget remaining and 1 trade slot
    # But max_per_trade caps at $200
    size = risk_mgr.max_position_size()
    assert size == 200.0


def test_status_report(risk_mgr):
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_close(50)
    status = risk_mgr.get_status()

    assert status["trades_taken"] == 1
    assert status["trades_remaining"] == 2
    assert status["realized_pnl"] == 50.0
    assert status["max_drawdown_hit"] is False
    assert status["target_hit"] is False
