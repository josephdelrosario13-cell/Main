"""Tests for the risk management module."""

import pytest
from datetime import date
from spx_bot.config import AccountConfig, AppConfig
from spx_bot.risk_manager import RiskManager


@pytest.fixture
def risk_mgr():
    cfg = AppConfig(
        account=AccountConfig(
            starting_capital=5000,
            daily_return_target_pct=20,
            max_daily_drawdown_pct=20,
            max_trades_per_day=4,
            max_per_trade=1200,
        )
    )
    return RiskManager(cfg)


def test_initial_state(risk_mgr):
    can, reason = risk_mgr.can_open_trade()
    assert can is True
    assert reason == "OK"


def test_max_trades_limit(risk_mgr):
    for _ in range(4):
        risk_mgr.record_trade_open()
    can, reason = risk_mgr.can_open_trade()
    assert can is False
    assert "Max trades" in reason


def test_drawdown_limit(risk_mgr):
    risk_mgr.record_trade_close(-500)
    risk_mgr.record_trade_close(-600)
    # Total loss: -1100, exceeds -1000 drawdown
    can, reason = risk_mgr.can_open_trade()
    assert can is False
    assert "drawdown" in reason.lower()


def test_target_hit(risk_mgr):
    risk_mgr.record_trade_close(600)
    risk_mgr.record_trade_close(500)
    # Total profit: 1100, exceeds 1000 target
    can, reason = risk_mgr.can_open_trade()
    assert can is False
    assert "target" in reason.lower()


def test_position_sizing(risk_mgr):
    size = risk_mgr.max_position_size()
    # $1000 remaining budget, capped at $1200 per trade -> $1000
    assert size == 1000.0

    # After a loss, budget shrinks
    risk_mgr.record_trade_close(-200)
    risk_mgr.record_trade_open()
    size = risk_mgr.max_position_size()
    # Remaining budget: $800, capped at $1200 -> $800
    assert size == 800.0


def test_contract_calculation(risk_mgr):
    # Max loss per contract = $350 (e.g., 5-wide spread, $1.50 credit)
    contracts = risk_mgr.calculate_contracts(350)
    # Budget = $1000, $1000 / $350 = 2 contracts
    assert contracts == 2

    # Smaller max loss
    contracts = risk_mgr.calculate_contracts(200)
    # $1000 / $200 = 5 contracts
    assert contracts == 5


def test_per_trade_cap(risk_mgr):
    # Simulate that we've already done 3 trades with profit
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_close(300)
    risk_mgr.record_trade_close(300)
    risk_mgr.record_trade_close(300)

    # With $1900 budget remaining and 1 trade slot, budget_per_trade = $1900
    # But max_per_trade caps at $1200
    size = risk_mgr.max_position_size()
    assert size == 1200.0


def test_status_report(risk_mgr):
    risk_mgr.record_trade_open()
    risk_mgr.record_trade_close(150)
    status = risk_mgr.get_status()

    assert status["trades_taken"] == 1
    assert status["trades_remaining"] == 3
    assert status["realized_pnl"] == 150.0
    assert status["max_drawdown_hit"] is False
    assert status["target_hit"] is False
