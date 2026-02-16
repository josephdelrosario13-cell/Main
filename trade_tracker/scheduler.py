"""Scheduler for automatic end-of-day trade pulls from IBKR.

Runs a background thread that triggers trade fetching at market close
(default 4:15 PM ET to allow final fills to settle).
"""

import logging
import threading
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from trade_tracker.database import TradeDatabase
from trade_tracker.ibkr_client import IBKRClient

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class EndOfDayScheduler:
    """Schedules daily trade pulls at a configurable time after market close."""

    def __init__(
        self,
        ibkr_client: IBKRClient,
        db: TradeDatabase,
        pull_time: dt_time = dt_time(16, 15),  # 4:15 PM ET
        account: str = "",
    ):
        self.client = ibkr_client
        self.db = db
        self.pull_time = pull_time
        self.account = account
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Start the scheduler in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Scheduler is already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="eod-scheduler",
        )
        self._thread.start()
        logger.info(
            "EOD scheduler started. Will pull trades at %s ET daily.",
            self.pull_time.strftime("%H:%M"),
        )

    def stop(self):
        """Stop the scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("EOD scheduler stopped")

    def pull_now(self) -> int:
        """Manually trigger a trade pull. Returns number of new trades."""
        logger.info("Pulling trades from IBKR...")
        executions = self.client.fetch_today_executions(account=self.account)

        if not executions:
            logger.info("No executions found")
            return 0

        self.db.store_executions(executions)
        new_trades = self.db.match_trades()
        logger.info(
            "Pulled %d executions, matched %d new trades",
            len(executions), len(new_trades),
        )
        return len(new_trades)

    def _run_loop(self):
        """Main scheduler loop. Checks every 30 seconds."""
        last_pull_date = None

        while not self._stop_event.is_set():
            now = datetime.now(ET)
            today = now.date()

            # Check if it's time to pull and we haven't pulled today
            if (
                now.time() >= self.pull_time
                and today != last_pull_date
                and now.weekday() < 5  # Mon-Fri only
            ):
                try:
                    count = self.pull_now()
                    last_pull_date = today
                    logger.info(
                        "EOD pull complete for %s: %d new trades",
                        today, count,
                    )
                except Exception:
                    logger.exception("EOD pull failed for %s", today)

            # Sleep 30 seconds between checks
            self._stop_event.wait(timeout=30)
