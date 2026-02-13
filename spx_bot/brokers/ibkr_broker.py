"""
Interactive Brokers (IBKR) broker implementation.

Connects to TWS or IB Gateway via the TWS socket API using ib_insync for:
- Real-time SPX market data and options chains
- Order execution for vertical spreads
- Account management

Requires TWS or IB Gateway running locally.
  - TWS paper trading: port 7497
  - TWS live:          port 7496
  - IB Gateway paper:  port 4002
  - IB Gateway live:   port 4001

Set IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID in .env.
"""

import logging
import time as time_mod
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import (
    IB,
    ComboLeg,
    Contract,
    Index,
    LimitOrder,
    Option,
    TagValue,
    util,
)

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
    SpreadSide,
)

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class IBKRConnection:
    """Manages the ib_insync IB connection."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self.account_id: Optional[str] = None

    def connect(self) -> bool:
        """Connect to TWS/IB Gateway."""
        try:
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=15,
            )
            accounts = self.ib.managedAccounts()
            if accounts:
                self.account_id = accounts[0]
                logger.info(
                    "IBKR connected to %s:%d | Account: %s",
                    self.host, self.port, self.account_id,
                )
                return True

            logger.error("No IBKR accounts found")
            return False

        except ConnectionRefusedError:
            logger.error(
                "Cannot connect to IBKR at %s:%d. "
                "Make sure TWS or IB Gateway is running and API connections are enabled "
                "(File > Global Configuration > API > Settings > Enable ActiveX and Socket Clients).",
                self.host, self.port,
            )
            return False
        except Exception as e:
            logger.error("IBKR connection failed: %s", e)
            return False

    def disconnect(self):
        """Disconnect from TWS/IB Gateway."""
        if self.ib.isConnected():
            self.ib.disconnect()

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()


class IBKRMarketData(MarketDataProvider):
    """Live market data from TWS via ib_insync."""

    def __init__(self, conn: IBKRConnection):
        self.conn = conn
        self.ib = conn.ib
        self._spx_contract = Index("SPX", "CBOE", "USD")
        self._vix_contract = Index("VIX", "CBOE", "USD")
        self._last_snapshot: Optional[SpxSnapshot] = None
        self._snapshot_cache_time = 0.0

    def connect(self) -> bool:
        if not self.conn.connected:
            if not self.conn.connect():
                return False

        # Qualify contracts so IBKR resolves them
        try:
            self.ib.qualifyContracts(self._spx_contract)
            self.ib.qualifyContracts(self._vix_contract)
            logger.info("SPX and VIX contracts qualified")
            return True
        except Exception as e:
            logger.error("Failed to qualify contracts: %s", e)
            return False

    def disconnect(self):
        self.conn.disconnect()

    def get_spx_snapshot(self) -> SpxSnapshot:
        now = time_mod.time()
        if self._last_snapshot and now - self._snapshot_cache_time < 2:
            return self._last_snapshot

        try:
            # Request market data
            spx_ticker = self.ib.reqMktData(self._spx_contract, "", False, False)
            vix_ticker = self.ib.reqMktData(self._vix_contract, "", False, False)

            # Give TWS a moment to send data
            self.ib.sleep(1)

            price = spx_ticker.marketPrice()
            if price != price:  # NaN check
                price = spx_ticker.last or spx_ticker.close or 0

            vix = vix_ticker.marketPrice()
            if vix != vix:
                vix = vix_ticker.last or vix_ticker.close or 18.0

            # Get daily bars for open/high/low
            bars = self.ib.reqHistoricalData(
                self._spx_contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            daily_open = price
            daily_high = price
            daily_low = price
            prev_close = price

            if bars:
                bar = bars[-1]
                daily_open = bar.open
                daily_high = bar.high
                daily_low = bar.low
                if len(bars) > 1:
                    prev_close = bars[-2].close
                else:
                    prev_close = bar.open

            snapshot = SpxSnapshot(
                price=round(price, 2),
                vix=round(vix, 2),
                timestamp=datetime.now(ET),
                daily_open=daily_open,
                daily_high=max(daily_high, price),
                daily_low=min(daily_low, price),
                prev_close=prev_close,
            )

            self._last_snapshot = snapshot
            self._snapshot_cache_time = now

            # Cancel streaming data to avoid hitting limits
            self.ib.cancelMktData(self._spx_contract)
            self.ib.cancelMktData(self._vix_contract)

            return snapshot

        except Exception as e:
            logger.error("IBKR snapshot error: %s", e)
            if self._last_snapshot:
                return self._last_snapshot
            raise

    def get_options_chain(self, expiration: Optional[date] = None) -> OptionsChain:
        exp = expiration or date.today()

        try:
            snapshot = self.get_spx_snapshot()

            # Get the option chain parameters
            chains = self.ib.reqSecDefOptParams(
                self._spx_contract.symbol,
                "",  # futFopExchange
                self._spx_contract.secType,
                self._spx_contract.conId,
            )

            if not chains:
                raise RuntimeError("No option chain data from IBKR")

            # Find the SMART/CBOE chain with our expiration
            target_exp = exp.strftime("%Y%m%d")
            valid_chain = None
            for chain in chains:
                if target_exp in chain.expirations:
                    valid_chain = chain
                    break

            if not valid_chain:
                # Try to find closest expiration
                logger.warning("Expiration %s not found, available: %s",
                               target_exp, chains[0].expirations[:5] if chains else "none")
                raise RuntimeError(f"Expiration {target_exp} not available")

            # Filter strikes near the money (±100 points)
            center = snapshot.price
            relevant_strikes = sorted([
                s for s in valid_chain.strikes
                if abs(s - center) <= 100
            ])

            calls = []
            puts = []

            # Build option contracts and request data in batches
            option_contracts = []
            for strike in relevant_strikes:
                for right in ["C", "P"]:
                    opt = Option(
                        "SPX", target_exp, strike, right,
                        exchange=valid_chain.exchange or "SMART",
                    )
                    option_contracts.append(opt)

            # Qualify all contracts at once (much faster than one at a time)
            qualified = self.ib.qualifyContracts(*option_contracts)

            # Request market data for all options
            tickers = []
            for contract in qualified:
                if contract.conId:  # Only request for successfully qualified contracts
                    ticker = self.ib.reqMktData(
                        contract,
                        genericTickList="106",  # Request implied volatility
                        snapshot=True,
                        regulatorySnapshot=False,
                    )
                    tickers.append((contract, ticker))

            # Wait for snapshot data
            self.ib.sleep(3)

            for contract, ticker in tickers:
                try:
                    bid = ticker.bid if ticker.bid and ticker.bid > 0 else 0
                    ask = ticker.ask if ticker.ask and ticker.ask > 0 else 0
                    last = ticker.last if ticker.last and ticker.last > 0 else 0

                    # Skip options with no valid pricing
                    if bid == 0 and ask == 0 and last == 0:
                        continue

                    opt_type = OptionType.CALL if contract.right == "C" else OptionType.PUT

                    greeks = None
                    if ticker.modelGreeks:
                        g = ticker.modelGreeks
                        greeks = Greeks(
                            delta=g.delta or 0,
                            gamma=g.gamma or 0,
                            theta=g.theta or 0,
                            vega=g.vega or 0,
                            iv=g.impliedVol or 0,
                        )

                    option = OptionQuote(
                        symbol=f"SPX_{contract.right}{contract.strike:.0f}",
                        underlying_price=snapshot.price,
                        strike=contract.strike,
                        option_type=opt_type,
                        expiration=exp,
                        bid=round(max(0, bid), 2),
                        ask=round(max(0, ask), 2),
                        last=round(max(0, last), 2),
                        volume=ticker.volume or 0,
                        open_interest=0,
                        greeks=greeks,
                    )

                    if opt_type == OptionType.CALL:
                        calls.append(option)
                    else:
                        puts.append(option)

                except Exception as e:
                    logger.debug("Failed to parse option %s: %s", contract.localSymbol, e)

            # Cancel market data subscriptions
            for contract, _ in tickers:
                try:
                    self.ib.cancelMktData(contract)
                except Exception:
                    pass

            logger.info(
                "IBKR chain loaded: %d calls, %d puts (SPX %.0f, exp %s)",
                len(calls), len(puts), snapshot.price, exp,
            )

            return OptionsChain(
                underlying_price=snapshot.price,
                expiration=exp,
                calls=sorted(calls, key=lambda x: x.strike),
                puts=sorted(puts, key=lambda x: x.strike),
                timestamp=datetime.now(ET),
            )

        except Exception as e:
            logger.error("IBKR chain error: %s", e)
            raise

    def get_vix(self) -> float:
        try:
            ticker = self.ib.reqMktData(self._vix_contract, "", True, False)
            self.ib.sleep(1)
            vix = ticker.marketPrice()
            self.ib.cancelMktData(self._vix_contract)
            return round(vix, 2) if vix == vix else 18.0
        except Exception:
            return 18.0

    def is_market_open(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)


class IBKROrderExecutor(OrderExecutor):
    """Execute spread orders through TWS via ib_insync."""

    def __init__(self, conn: IBKRConnection):
        self.conn = conn
        self.ib = conn.ib

    def submit_order(self, order: Order) -> Fill:
        if not self.conn.connected:
            raise RuntimeError("IBKR not connected")

        account = self.conn.account_id

        # Build the combo (BAG) contract for the vertical spread
        combo = Contract()
        combo.symbol = "SPX"
        combo.secType = "BAG"
        combo.currency = "USD"
        combo.exchange = "SMART"

        # Qualify individual legs to get conIds
        leg_contracts = []
        for leg in order.legs:
            right = "C" if leg.option_type == "call" else "P"
            exp_str = date.today().strftime("%Y%m%d")
            opt = Option("SPX", exp_str, leg.strike, right, "SMART")
            self.ib.qualifyContracts(opt)
            leg_contracts.append((leg, opt))

        combo.comboLegs = []
        for leg, opt_contract in leg_contracts:
            combo_leg = ComboLeg()
            combo_leg.conId = opt_contract.conId
            combo_leg.ratio = 1
            combo_leg.action = leg.action  # "BUY" or "SELL"
            combo_leg.exchange = "SMART"
            combo.comboLegs.append(combo_leg)

        # Create limit order
        action = "SELL" if order.order_type == OrderType.CREDIT else "BUY"
        limit_order = LimitOrder(
            action=action,
            totalQuantity=order.quantity,
            lmtPrice=order.limit_price,
            account=account,
            tif="DAY",
        )

        try:
            trade = self.ib.placeOrder(combo, limit_order)
            order.order_id = str(trade.order.orderId)
            order.status = OrderStatus.SUBMITTED

            logger.info(
                "IBKR order submitted: %s | %s x%d @ $%.2f",
                order.order_id,
                order.spread_side.value,
                order.quantity,
                order.limit_price,
            )

            # Wait for fill
            return self._wait_for_fill(trade, order)

        except Exception as e:
            logger.error("IBKR order failed: %s", e)
            raise

    def _wait_for_fill(self, trade, order: Order, timeout: int = 30) -> Fill:
        """Wait for the order to fill."""
        start = time_mod.time()

        while time_mod.time() - start < timeout:
            self.ib.sleep(1)

            if trade.isDone():
                if trade.orderStatus.status == "Filled":
                    return Fill(
                        order_id=str(trade.order.orderId),
                        fill_price=trade.orderStatus.avgFillPrice,
                        quantity=int(trade.orderStatus.filled),
                        timestamp=datetime.now(ET),
                        status=OrderStatus.FILLED,
                    )
                else:
                    raise RuntimeError(
                        f"IBKR order {trade.order.orderId} ended with status: "
                        f"{trade.orderStatus.status}"
                    )

            # Log intermediate status
            status = trade.orderStatus.status
            if status not in ("PendingSubmit", "PreSubmitted", "Submitted"):
                logger.warning("IBKR order unexpected status: %s", status)

        # Timeout - cancel
        self.ib.cancelOrder(trade.order)
        self.ib.sleep(2)
        raise TimeoutError(
            f"IBKR order {trade.order.orderId} not filled within {timeout}s"
        )

    def cancel_order(self, order_id: str) -> bool:
        try:
            for trade in self.ib.openTrades():
                if str(trade.order.orderId) == order_id:
                    self.ib.cancelOrder(trade.order)
                    self.ib.sleep(1)
                    return True
            return False
        except Exception as e:
            logger.error("IBKR cancel failed: %s", e)
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        try:
            for trade in self.ib.trades():
                if str(trade.order.orderId) == order_id:
                    status = trade.orderStatus.status
                    status_map = {
                        "PendingSubmit": OrderStatus.PENDING,
                        "PreSubmitted": OrderStatus.PENDING,
                        "Submitted": OrderStatus.SUBMITTED,
                        "Filled": OrderStatus.FILLED,
                        "Cancelled": OrderStatus.CANCELLED,
                        "Inactive": OrderStatus.REJECTED,
                    }
                    return status_map.get(status, OrderStatus.PENDING)
        except Exception:
            pass
        return OrderStatus.PENDING
