"""
Interactive Brokers (IBKR) broker implementation.

Connects to TWS or IB Gateway via the IBKR Client Portal API for:
- Real-time SPX market data and options chains
- Order execution for vertical spreads
- Account management

Requires TWS or IB Gateway running locally.
Set IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID in .env.
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


class IBKRClient:
    """Low-level client for the IBKR Client Portal API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5000):
        self.base_url = f"https://{host}:{port}/v1/api"
        self._session = requests.Session()
        self._session.verify = False  # IBKR gateway uses self-signed certs
        self.account_id: Optional[str] = None

    def connect(self) -> bool:
        """Verify connection to IBKR gateway and get account."""
        try:
            # Check auth status
            resp = self._session.get(
                f"{self.base_url}/iserver/auth/status",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("authenticated"):
                logger.error("IBKR gateway not authenticated. Open TWS/Gateway and log in.")
                return False

            # Get accounts
            resp = self._session.get(
                f"{self.base_url}/portfolio/accounts",
                timeout=10,
            )
            resp.raise_for_status()
            accounts = resp.json()

            if accounts:
                self.account_id = accounts[0]["id"]
                logger.info("IBKR connected. Account: %s", self.account_id)
                return True

            logger.error("No IBKR accounts found")
            return False

        except requests.ConnectionError:
            logger.error(
                "Cannot connect to IBKR gateway at %s. "
                "Make sure TWS or IB Gateway is running with Client Portal API enabled.",
                self.base_url,
            )
            return False
        except Exception as e:
            logger.error("IBKR connection failed: %s", e)
            return False

    def get(self, endpoint: str, params: dict = None, timeout: int = 10) -> dict:
        resp = self._session.get(
            f"{self.base_url}{endpoint}",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def post(self, endpoint: str, json: dict = None, timeout: int = 10) -> dict:
        resp = self._session.post(
            f"{self.base_url}{endpoint}",
            json=json,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def delete(self, endpoint: str, timeout: int = 10) -> bool:
        resp = self._session.delete(
            f"{self.base_url}{endpoint}",
            timeout=timeout,
        )
        return resp.status_code in (200, 204)


# SPX conid (contract ID) — this is the standard IBKR conid for SPX index
SPX_CONID = 416904
VIX_CONID = 13455763


class IBKRMarketData(MarketDataProvider):
    """Live market data from IBKR Client Portal API."""

    def __init__(self, client: IBKRClient):
        self.client = client
        self._connected = False
        self._last_snapshot: Optional[SpxSnapshot] = None
        self._snapshot_cache_time = 0.0

    def connect(self) -> bool:
        if not self.client.account_id:
            if not self.client.connect():
                return False
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def get_spx_snapshot(self) -> SpxSnapshot:
        now = time_mod.time()
        if self._last_snapshot and now - self._snapshot_cache_time < 2:
            return self._last_snapshot

        try:
            # Get SPX market data snapshot
            data = self.client.get(
                f"/iserver/marketdata/snapshot",
                params={"conids": str(SPX_CONID), "fields": "31,70,71,82,83,84,85,86"},
            )

            spx_data = data[0] if data else {}

            # Field mapping: 31=last, 70=high, 71=low, 82=change, 83=change%,
            # 84=bid, 85=ask, 86=volume
            price = float(spx_data.get("31", 0))
            high = float(spx_data.get("70", price))
            low = float(spx_data.get("71", price))

            # Get VIX
            vix_data = self.client.get(
                f"/iserver/marketdata/snapshot",
                params={"conids": str(VIX_CONID), "fields": "31"},
            )
            vix = float(vix_data[0].get("31", 18.0)) if vix_data else 18.0

            # Compute open from change
            change = float(spx_data.get("82", 0))
            prev_close = price - change

            snapshot = SpxSnapshot(
                price=price,
                vix=vix,
                timestamp=datetime.now(ET),
                daily_open=prev_close + change * 0.1,  # Approximate
                daily_high=high,
                daily_low=low,
                prev_close=prev_close,
            )

            self._last_snapshot = snapshot
            self._snapshot_cache_time = now
            return snapshot

        except Exception as e:
            logger.error("IBKR snapshot error: %s", e)
            if self._last_snapshot:
                return self._last_snapshot
            raise

    def get_options_chain(self, expiration: Optional[date] = None) -> OptionsChain:
        exp = expiration or date.today()
        exp_str = exp.strftime("%Y%m%d")

        try:
            snapshot = self.get_spx_snapshot()

            # Get the option chain info
            chain_info = self.client.get(
                f"/iserver/secdef/info",
                params={
                    "conid": str(SPX_CONID),
                    "sectype": "OPT",
                    "month": exp_str[:6],
                },
            )

            # Search for options matching our expiry
            search_data = self.client.post(
                "/iserver/secdef/search",
                json={"symbol": "SPX", "secType": "OPT"},
            )

            calls = []
            puts = []

            # Get strikes from the chain
            strikes_resp = self.client.get(
                f"/iserver/secdef/strikes",
                params={
                    "conid": str(SPX_CONID),
                    "sectype": "OPT",
                    "month": exp_str[:6],
                    "exchange": "SMART",
                },
            )

            call_strikes = strikes_resp.get("call", [])
            put_strikes = strikes_resp.get("put", [])

            # Filter to strikes near current price (±100 points)
            center = snapshot.price
            relevant_strikes = [
                s for s in set(call_strikes + put_strikes)
                if abs(s - center) <= 100
            ]

            for strike in sorted(relevant_strikes):
                # Get conids for each strike
                for right in ["C", "P"]:
                    try:
                        opt_info = self.client.get(
                            "/iserver/secdef/info",
                            params={
                                "conid": str(SPX_CONID),
                                "sectype": "OPT",
                                "month": exp_str[:6],
                                "strike": str(strike),
                                "right": right,
                            },
                        )

                        if not opt_info:
                            continue

                        opt_conid = opt_info[0].get("conid") if opt_info else None
                        if not opt_conid:
                            continue

                        # Get quote for this option
                        quote_data = self.client.get(
                            "/iserver/marketdata/snapshot",
                            params={
                                "conids": str(opt_conid),
                                "fields": "31,84,85,86,7059,7057,7058,7060",
                            },
                        )

                        if not quote_data:
                            continue

                        q = quote_data[0]
                        bid = float(q.get("84", 0))
                        ask = float(q.get("85", 0))
                        last = float(q.get("31", 0))
                        volume = int(float(q.get("86", 0)))

                        opt_type = OptionType.CALL if right == "C" else OptionType.PUT

                        greeks = None
                        delta = q.get("7059")
                        if delta is not None:
                            greeks = Greeks(
                                delta=float(delta),
                                gamma=float(q.get("7057", 0)),
                                theta=float(q.get("7058", 0)),
                                vega=float(q.get("7060", 0)),
                                iv=float(q.get("7084", 0.2)),
                            )

                        option = OptionQuote(
                            symbol=f"SPX_{right}{strike:.0f}",
                            underlying_price=snapshot.price,
                            strike=strike,
                            option_type=opt_type,
                            expiration=exp,
                            bid=bid,
                            ask=ask,
                            last=last,
                            volume=volume,
                            open_interest=0,
                            greeks=greeks,
                        )

                        if opt_type == OptionType.CALL:
                            calls.append(option)
                        else:
                            puts.append(option)

                    except Exception as e:
                        logger.debug("Failed to get option data for %s %.0f: %s", right, strike, e)

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
            data = self.client.get(
                "/iserver/marketdata/snapshot",
                params={"conids": str(VIX_CONID), "fields": "31"},
            )
            return float(data[0].get("31", 18.0)) if data else 18.0
        except Exception:
            return 18.0

    def is_market_open(self) -> bool:
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        return time(9, 30) <= now.time() <= time(16, 0)


class IBKROrderExecutor(OrderExecutor):
    """Execute orders through IBKR Client Portal API."""

    def __init__(self, client: IBKRClient):
        self.client = client

    def submit_order(self, order: Order) -> Fill:
        account = self.client.account_id
        if not account:
            raise RuntimeError("IBKR not connected")

        # Build combo order legs
        legs = []
        for leg in order.legs:
            legs.append({
                "conid": 0,  # Will be resolved by symbol
                "side": "SELL" if leg.action == "SELL" else "BUY",
                "quantity": leg.quantity,
            })

        # For IBKR, we submit as a combo/bag order
        payload = {
            "orders": [{
                "acctId": account,
                "conid": SPX_CONID,
                "secType": f"SPX COMBO",
                "orderType": "LMT",
                "side": "SELL" if order.order_type == OrderType.CREDIT else "BUY",
                "quantity": order.quantity,
                "price": order.limit_price,
                "tif": "DAY",
                "legs": legs,
            }]
        }

        try:
            resp = self.client.post(
                f"/iserver/account/{account}/orders",
                json=payload,
            )

            # IBKR may return order confirmation questions
            if isinstance(resp, list) and resp and resp[0].get("id"):
                # Confirm the order
                confirm_id = resp[0]["id"]
                resp = self.client.post(
                    f"/iserver/reply/{confirm_id}",
                    json={"confirmed": True},
                )

            order_id = str(resp[0].get("order_id", "")) if isinstance(resp, list) else ""
            order.order_id = order_id
            order.status = OrderStatus.SUBMITTED

            logger.info("IBKR order submitted: %s", order_id)

            # Wait for fill
            return self._wait_for_fill(order_id, order)

        except Exception as e:
            logger.error("IBKR order failed: %s", e)
            raise

    def _wait_for_fill(self, order_id: str, order: Order, timeout: int = 30) -> Fill:
        account = self.client.account_id
        start = time_mod.time()

        while time_mod.time() - start < timeout:
            try:
                data = self.client.get(f"/iserver/account/orders")

                for o in data.get("orders", []):
                    if str(o.get("orderId")) == order_id:
                        status = o.get("status", "").lower()

                        if status == "filled":
                            return Fill(
                                order_id=order_id,
                                fill_price=float(o.get("avgPrice", order.limit_price)),
                                quantity=order.quantity,
                                timestamp=datetime.now(ET),
                                status=OrderStatus.FILLED,
                            )

                        if status in ("cancelled", "inactive"):
                            raise RuntimeError(f"IBKR order {order_id} {status}")

            except requests.RequestException as e:
                logger.warning("IBKR order status error: %s", e)

            time_mod.sleep(1)

        self.cancel_order(order_id)
        raise TimeoutError(f"IBKR order {order_id} not filled within {timeout}s")

    def cancel_order(self, order_id: str) -> bool:
        account = self.client.account_id
        try:
            return self.client.delete(
                f"/iserver/account/{account}/order/{order_id}",
            )
        except Exception as e:
            logger.error("IBKR cancel failed: %s", e)
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        try:
            data = self.client.get("/iserver/account/orders")
            for o in data.get("orders", []):
                if str(o.get("orderId")) == order_id:
                    status = o.get("status", "").lower()
                    status_map = {
                        "presubmitted": OrderStatus.PENDING,
                        "submitted": OrderStatus.SUBMITTED,
                        "filled": OrderStatus.FILLED,
                        "cancelled": OrderStatus.CANCELLED,
                        "inactive": OrderStatus.REJECTED,
                    }
                    return status_map.get(status, OrderStatus.PENDING)
        except Exception:
            pass
        return OrderStatus.PENDING
