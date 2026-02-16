"""IBKR client for fetching trade executions via TWS/Gateway API.

Requires Interactive Brokers TWS or IB Gateway running locally.
Uses ib_insync for the connection.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import IB, ExecutionFilter

from trade_tracker.models import AssetClass, Execution, Side

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# Map IBKR secType strings to our AssetClass enum
_ASSET_CLASS_MAP = {
    "STK": AssetClass.STOCK,
    "OPT": AssetClass.OPTION,
    "FUT": AssetClass.FUTURE,
    "CASH": AssetClass.FOREX,
}


class IBKRClient:
    """Connects to TWS/Gateway and fetches execution data."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 10,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()

    def connect(self) -> bool:
        """Connect to TWS/Gateway. Returns True on success."""
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            logger.info(
                "Connected to IBKR at %s:%d (client %d)",
                self.host, self.port, self.client_id,
            )
            return True
        except Exception:
            logger.exception("Failed to connect to IBKR")
            return False

    def disconnect(self):
        """Disconnect from TWS/Gateway."""
        if self.ib.isConnected():
            self.ib.disconnect()
            logger.info("Disconnected from IBKR")

    def fetch_executions(
        self,
        account: str = "",
        since: Optional[datetime] = None,
    ) -> list[Execution]:
        """Fetch execution reports from IBKR.

        Args:
            account: Filter by account ID (empty string = all accounts).
            since: Only return executions after this time.
                   Defaults to start of today (ET).

        Returns:
            List of Execution objects.
        """
        if not self.ib.isConnected():
            if not self.connect():
                return []

        if since is None:
            since = datetime.now(ET).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )

        # IBKR expects the time as a string in yyyymmdd-HH:MM:SS format
        time_str = since.strftime("%Y%m%d-00:00:00")

        exec_filter = ExecutionFilter(
            acctCode=account,
            time=time_str,
        )

        fills = self.ib.reqExecutions(exec_filter)
        executions = []

        for fill in fills:
            contract = fill.contract
            execution = fill.execution
            commission_report = fill.commissionReport

            asset_class = _ASSET_CLASS_MAP.get(
                contract.secType, AssetClass.OTHER,
            )

            # Parse execution time
            exec_time = datetime.strptime(
                execution.time, "%Y%m%d-%H:%M:%S",
            ).replace(tzinfo=ET)

            commission = 0.0
            realized_pnl = None
            if commission_report:
                commission = commission_report.commission or 0.0
                rpnl = commission_report.realizedPNL
                if rpnl and rpnl < 1e9:  # IBKR uses 1.7976931e+308 for N/A
                    realized_pnl = rpnl

            symbol = contract.localSymbol or contract.symbol

            executions.append(Execution(
                exec_id=execution.execId,
                order_id=execution.orderId,
                account=execution.acctNumber,
                symbol=symbol,
                side=Side.BUY if execution.side == "BOT" else Side.SELL,
                quantity=abs(execution.shares),
                price=execution.price,
                timestamp=exec_time,
                asset_class=asset_class,
                exchange=execution.exchange,
                commission=commission,
                realized_pnl=realized_pnl,
            ))

        logger.info("Fetched %d executions from IBKR", len(executions))
        return executions

    def fetch_today_executions(self, account: str = "") -> list[Execution]:
        """Convenience method to fetch today's executions."""
        today_start = datetime.now(ET).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return self.fetch_executions(account=account, since=today_start)

    def fetch_executions_for_date(
        self, target_date: datetime, account: str = "",
    ) -> list[Execution]:
        """Fetch executions for a specific date.

        Note: IBKR only retains execution data for the past 7 days via API.
        For older data, use Flex Queries.
        """
        start = target_date.replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=ET,
        )
        end = start + timedelta(days=1)

        all_execs = self.fetch_executions(account=account, since=start)
        return [e for e in all_execs if e.timestamp < end]
