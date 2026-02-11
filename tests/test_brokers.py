"""Tests for broker integrations."""

import pytest
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from spx_bot.brokers.paper_broker import PaperMarketData, PaperOrderExecutor
from spx_bot.market_data import OptionType
from spx_bot.orders import Order, OrderType, SpreadLeg, SpreadSide

ET = ZoneInfo("America/New_York")


# ─── Paper Broker Tests ──────────────────────────────────────────────

class TestPaperBroker:
    def test_connect(self):
        md = PaperMarketData()
        assert md.connect() is True

    def test_snapshot_returns_valid_data(self):
        md = PaperMarketData(base_price=5800.0, base_vix=18.0)
        md.connect()
        snapshot = md.get_spx_snapshot()
        assert 5700 < snapshot.price < 5900
        assert 10 < snapshot.vix < 50

    def test_options_chain_has_puts_and_calls(self):
        md = PaperMarketData(base_price=5800.0)
        md.connect()
        chain = md.get_options_chain()
        assert len(chain.calls) > 0
        assert len(chain.puts) > 0
        assert chain.underlying_price > 0

    def test_chain_find_by_delta(self):
        md = PaperMarketData(base_price=5800.0, base_vix=18.0)
        md.connect()
        chain = md.get_options_chain()
        put = chain.find_by_delta(OptionType.PUT, -0.16)
        assert put is not None
        assert put.greeks is not None
        assert put.greeks.delta < 0

    def test_chain_find_by_strike(self):
        md = PaperMarketData(base_price=5800.0)
        md.connect()
        chain = md.get_options_chain()
        center = round(5800.0 / 5) * 5
        put = chain.find_by_strike(OptionType.PUT, center)
        assert put is not None
        assert put.strike == center

    def test_paper_order_execution(self):
        md = PaperMarketData(base_price=5800.0)
        md.connect()
        executor = PaperOrderExecutor(md)

        order = Order(
            spread_side=SpreadSide.BULL_PUT,
            legs=[
                SpreadLeg("SPX_P5770", 5770, "SELL", 2, "put"),
                SpreadLeg("SPX_P5765", 5765, "BUY", 2, "put"),
            ],
            quantity=2,
            order_type=OrderType.CREDIT,
            limit_price=1.50,
        )

        fill = executor.submit_order(order)
        assert fill.quantity == 2
        assert fill.fill_price > 0
        assert order.order_id is not None

    def test_paper_cancel(self):
        md = PaperMarketData()
        executor = PaperOrderExecutor(md)
        assert executor.cancel_order("PAPER-1") is True


# ─── Tastytrade Broker Tests ─────────────────────────────────────────

class TestTastytradeBroker:
    def test_auth_login_success(self):
        from spx_bot.brokers.tastytrade_broker import TastytradeAuth

        auth = TastytradeAuth("testuser", "testpass")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {"session-token": "test-token-123"}
        }
        mock_resp.raise_for_status = MagicMock()

        accounts_resp = MagicMock()
        accounts_resp.json.return_value = {
            "data": {"items": [{"account": {"account-number": "ABC123"}}]}
        }
        accounts_resp.raise_for_status = MagicMock()

        with patch.object(auth._session, "post", return_value=mock_resp):
            with patch.object(auth._session, "get", return_value=accounts_resp):
                result = auth.login()

        assert result is True
        assert auth.session_token == "test-token-123"
        assert auth.account_number == "ABC123"

    def test_auth_login_failure(self):
        from spx_bot.brokers.tastytrade_broker import TastytradeAuth

        auth = TastytradeAuth("bad", "bad")

        with patch.object(auth._session, "post", side_effect=Exception("401")):
            result = auth.login()

        assert result is False

    def test_market_data_connect(self):
        from spx_bot.brokers.tastytrade_broker import TastytradeAuth, TastytradeMarketData

        auth = MagicMock(spec=TastytradeAuth)
        auth.session_token = "token"
        auth._session = MagicMock()

        md = TastytradeMarketData(auth)
        assert md.connect() is True

    def test_order_executor_requires_account(self):
        from spx_bot.brokers.tastytrade_broker import TastytradeAuth, TastytradeOrderExecutor

        auth = MagicMock(spec=TastytradeAuth)
        auth.account_number = None
        auth._session = MagicMock()

        executor = TastytradeOrderExecutor(auth)
        order = Order(
            spread_side=SpreadSide.BULL_PUT,
            legs=[
                SpreadLeg("SPX_P5770", 5770, "SELL", 1, "put"),
                SpreadLeg("SPX_P5765", 5765, "BUY", 1, "put"),
            ],
            quantity=1,
            order_type=OrderType.CREDIT,
            limit_price=1.50,
        )

        with pytest.raises(RuntimeError, match="No account"):
            executor.submit_order(order)


# ─── IBKR Broker Tests ───────────────────────────────────────────────

class TestIBKRBroker:
    def test_client_connect_success(self):
        from spx_bot.brokers.ibkr_broker import IBKRClient

        client = IBKRClient()

        auth_resp = MagicMock()
        auth_resp.json.return_value = {"authenticated": True}
        auth_resp.raise_for_status = MagicMock()

        accounts_resp = MagicMock()
        accounts_resp.json.return_value = [{"id": "U1234567"}]
        accounts_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", side_effect=[auth_resp, accounts_resp]):
            result = client.connect()

        assert result is True
        assert client.account_id == "U1234567"

    def test_client_connect_not_authenticated(self):
        from spx_bot.brokers.ibkr_broker import IBKRClient

        client = IBKRClient()

        auth_resp = MagicMock()
        auth_resp.json.return_value = {"authenticated": False}
        auth_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=auth_resp):
            result = client.connect()

        assert result is False

    def test_order_executor_requires_connection(self):
        from spx_bot.brokers.ibkr_broker import IBKRClient, IBKROrderExecutor

        client = IBKRClient()
        client.account_id = None
        executor = IBKROrderExecutor(client)

        order = Order(
            spread_side=SpreadSide.BULL_PUT,
            legs=[
                SpreadLeg("SPX_P5770", 5770, "SELL", 1, "put"),
                SpreadLeg("SPX_P5765", 5765, "BUY", 1, "put"),
            ],
            quantity=1,
            order_type=OrderType.CREDIT,
            limit_price=1.50,
        )

        with pytest.raises(RuntimeError, match="not connected"):
            executor.submit_order(order)
