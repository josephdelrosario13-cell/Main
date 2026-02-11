"""
Tastytrade broker implementation.

Connects to the Tastytrade API for:
- Real-time SPX market data and options chains
- Order execution for vertical spreads
- Account and position management

Requires TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD in .env.
"""

import logging
import time as time_mod
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

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

API_BASE = "https://api.tastyworks.com"
STREAMER_BASE = "https://streamer.cert.tastyworks.com"


class TastytradeAuth:
    """Handle Tastytrade API authentication."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session_token: Optional[str] = None
        self.account_number: Optional[str] = None
        self._session = requests.Session()

    def login(self) -> bool:
        """Authenticate and obtain a session token."""
        try:
            resp = self._session.post(
                f"{API_BASE}/sessions",
                json={
                    "login": self.username,
                    "password": self.password,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            self.session_token = data["session-token"]
            self._session.headers["Authorization"] = self.session_token
            logger.info("Tastytrade login successful for %s", self.username)

            # Fetch first account
            self._fetch_account()
            return True
        except Exception as e:
            logger.error("Tastytrade login failed: %s", e)
            return False

    def _fetch_account(self):
        """Get the first trading account."""
        resp = self._session.get(
            f"{API_BASE}/customers/me/accounts",
            timeout=10,
        )
        resp.raise_for_status()
        accounts = resp.json()["data"]["items"]
        if accounts:
            self.account_number = accounts[0]["account"]["account-number"]
            logger.info("Using account: %s", self.account_number)
        else:
            raise ValueError("No trading accounts found")

    @property
    def headers(self) -> dict:
        return {"Authorization": self.session_token} if self.session_token else {}


class TastytradeMarketData(MarketDataProvider):
    """Live market data from Tastytrade API."""

    def __init__(self, auth: TastytradeAuth):
        self.auth = auth
        self._session = auth._session
        self._connected = False
        self._last_snapshot: Optional[SpxSnapshot] = None
        self._snapshot_cache_time = 0.0

    def connect(self) -> bool:
        if not self.auth.session_token:
            if not self.auth.login():
                return False
        self._connected = True
        logger.info("Tastytrade market data connected")
        return True

    def disconnect(self):
        self._connected = False

    def get_spx_snapshot(self) -> SpxSnapshot:
        """Fetch current SPX quote data."""
        now = time_mod.time()
        # Cache for 2 seconds to avoid rate limiting
        if self._last_snapshot and now - self._snapshot_cache_time < 2:
            return self._last_snapshot

        try:
            # Get SPX quote
            resp = self._session.get(
                f"{API_BASE}/market-data/SPX/quotes",
                timeout=10,
            )
            resp.raise_for_status()
            quote = resp.json()["data"]

            # Get VIX
            vix_resp = self._session.get(
                f"{API_BASE}/market-data/VIX/quotes",
                timeout=10,
            )
            vix_resp.raise_for_status()
            vix_data = vix_resp.json()["data"]

            price = float(quote.get("last", quote.get("mid", 0)))
            snapshot = SpxSnapshot(
                price=price,
                vix=float(vix_data.get("last", 18.0)),
                timestamp=datetime.now(ET),
                daily_open=float(quote.get("open", price)),
                daily_high=float(quote.get("high", price)),
                daily_low=float(quote.get("low", price)),
                prev_close=float(quote.get("prev-close", price)),
            )

            self._last_snapshot = snapshot
            self._snapshot_cache_time = now
            return snapshot

        except Exception as e:
            logger.error("Failed to get SPX snapshot: %s", e)
            if self._last_snapshot:
                return self._last_snapshot
            raise

    def get_options_chain(self, expiration: Optional[date] = None) -> OptionsChain:
        """Fetch the SPX 0DTE options chain from Tastytrade."""
        exp = expiration or date.today()
        exp_str = exp.strftime("%Y-%m-%d")

        try:
            resp = self._session.get(
                f"{API_BASE}/option-chains/SPX/nested",
                params={"expiration": exp_str},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()["data"]["items"]

            calls = []
            puts = []
            snapshot = self.get_spx_snapshot()

            for item in data:
                for expiration_data in item.get("expirations", []):
                    if expiration_data.get("expiration-date") != exp_str:
                        continue

                    for strike_data in expiration_data.get("strikes", []):
                        strike = float(strike_data["strike-price"])

                        # Parse call
                        call_info = strike_data.get("call")
                        if call_info:
                            call_quote = self._parse_option_quote(
                                call_info, strike, OptionType.CALL,
                                exp, snapshot.price,
                            )
                            if call_quote:
                                calls.append(call_quote)

                        # Parse put
                        put_info = strike_data.get("put")
                        if put_info:
                            put_quote = self._parse_option_quote(
                                put_info, strike, OptionType.PUT,
                                exp, snapshot.price,
                            )
                            if put_quote:
                                puts.append(put_quote)

            return OptionsChain(
                underlying_price=snapshot.price,
                expiration=exp,
                calls=sorted(calls, key=lambda x: x.strike),
                puts=sorted(puts, key=lambda x: x.strike),
                timestamp=datetime.now(ET),
            )

        except Exception as e:
            logger.error("Failed to get options chain: %s", e)
            raise

    def _parse_option_quote(
        self, data: dict, strike: float, opt_type: OptionType,
        exp: date, underlying_price: float,
    ) -> Optional[OptionQuote]:
        """Parse a single option quote from the API response."""
        try:
            symbol = data.get("symbol", f"SPX_{opt_type.value[0].upper()}{strike:.0f}")
            bid = float(data.get("bid", 0))
            ask = float(data.get("ask", 0))
            last = float(data.get("last", 0))

            greeks = None
            greeks_data = data.get("greeks")
            if greeks_data:
                greeks = Greeks(
                    delta=float(greeks_data.get("delta", 0)),
                    gamma=float(greeks_data.get("gamma", 0)),
                    theta=float(greeks_data.get("theta", 0)),
                    vega=float(greeks_data.get("vega", 0)),
                    iv=float(greeks_data.get("implied-volatility", 0)),
                )

            return OptionQuote(
                symbol=symbol,
                underlying_price=underlying_price,
                strike=strike,
                option_type=opt_type,
                expiration=exp,
                bid=bid,
                ask=ask,
                last=last,
                volume=int(data.get("volume", 0)),
                open_interest=int(data.get("open-interest", 0)),
                greeks=greeks,
            )
        except Exception as e:
            logger.debug("Failed to parse option at strike %.0f: %s", strike, e)
            return None

    def get_vix(self) -> float:
        try:
            resp = self._session.get(
                f"{API_BASE}/market-data/VIX/quotes",
                timeout=10,
            )
            resp.raise_for_status()
            return float(resp.json()["data"].get("last", 18.0))
        except Exception:
            return 18.0

    def is_market_open(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)


class TastytradeOrderExecutor(OrderExecutor):
    """Execute orders through the Tastytrade API."""

    def __init__(self, auth: TastytradeAuth):
        self.auth = auth
        self._session = auth._session

    def submit_order(self, order: Order) -> Fill:
        """Submit a vertical spread order to Tastytrade."""
        account = self.auth.account_number
        if not account:
            raise RuntimeError("No account number - login first")

        # Build the order payload
        legs = []
        for leg in order.legs:
            legs.append({
                "instrument-type": "Equity Option",
                "symbol": leg.symbol,
                "action": "Sell to Open" if leg.action == "SELL" else "Buy to Open",
                "quantity": leg.quantity,
            })

            # For closing orders, use close actions
            if order.order_type == OrderType.DEBIT:
                legs[-1]["action"] = (
                    "Buy to Close" if leg.action == "BUY" else "Sell to Close"
                )

        payload = {
            "time-in-force": "Day",
            "order-type": "Limit",
            "price": str(order.limit_price),
            "price-effect": "Credit" if order.order_type == OrderType.CREDIT else "Debit",
            "legs": legs,
        }

        try:
            resp = self._session.post(
                f"{API_BASE}/accounts/{account}/orders",
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()["data"]["order"]

            order_id = str(data.get("id", ""))
            order.order_id = order_id
            order.status = OrderStatus.SUBMITTED

            logger.info(
                "Tastytrade order submitted: %s | %s x%d @ $%.2f",
                order_id,
                order.spread_side.value,
                order.quantity,
                order.limit_price,
            )

            # Wait for fill (poll with timeout)
            fill = self._wait_for_fill(order_id, order, timeout=30)
            return fill

        except Exception as e:
            logger.error("Tastytrade order submission failed: %s", e)
            raise

    def _wait_for_fill(self, order_id: str, order: Order, timeout: int = 30) -> Fill:
        """Poll for order fill status."""
        account = self.auth.account_number
        start = time_mod.time()

        while time_mod.time() - start < timeout:
            try:
                resp = self._session.get(
                    f"{API_BASE}/accounts/{account}/orders/{order_id}",
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()["data"]

                status = data.get("status", "").lower()

                if status == "filled":
                    fill_price = float(data.get("price", order.limit_price))
                    return Fill(
                        order_id=order_id,
                        fill_price=fill_price,
                        quantity=order.quantity,
                        timestamp=datetime.now(ET),
                        status=OrderStatus.FILLED,
                    )

                if status in ("cancelled", "rejected", "expired"):
                    raise RuntimeError(f"Order {order_id} {status}")

            except requests.RequestException as e:
                logger.warning("Error checking order status: %s", e)

            time_mod.sleep(1)

        # Timeout — cancel and raise
        self.cancel_order(order_id)
        raise TimeoutError(f"Order {order_id} not filled within {timeout}s")

    def cancel_order(self, order_id: str) -> bool:
        account = self.auth.account_number
        try:
            resp = self._session.delete(
                f"{API_BASE}/accounts/{account}/orders/{order_id}",
                timeout=10,
            )
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error("Failed to cancel order %s: %s", order_id, e)
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        account = self.auth.account_number
        try:
            resp = self._session.get(
                f"{API_BASE}/accounts/{account}/orders/{order_id}",
                timeout=10,
            )
            resp.raise_for_status()
            status = resp.json()["data"].get("status", "").lower()
            status_map = {
                "received": OrderStatus.SUBMITTED,
                "routed": OrderStatus.SUBMITTED,
                "in-flight": OrderStatus.SUBMITTED,
                "live": OrderStatus.SUBMITTED,
                "partially-filled": OrderStatus.PARTIAL,
                "filled": OrderStatus.FILLED,
                "cancelled": OrderStatus.CANCELLED,
                "rejected": OrderStatus.REJECTED,
                "expired": OrderStatus.CANCELLED,
            }
            return status_map.get(status, OrderStatus.PENDING)
        except Exception:
            return OrderStatus.PENDING
