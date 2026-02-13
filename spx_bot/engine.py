"""
Main trading engine / orchestrator.

This is the central loop that ties together:
- Market data polling
- Signal generation
- Risk checks
- Order execution
- Position monitoring
- Logging and alerts
"""

import logging
import signal
import sys
import threading
import time as time_mod
from datetime import datetime, time
from zoneinfo import ZoneInfo

from spx_bot.config import AppConfig, BrokerType, config
from spx_bot.brokers.paper_broker import PaperMarketData, PaperOrderExecutor
from spx_bot.brokers.tastytrade_broker import TastytradeAuth, TastytradeMarketData, TastytradeOrderExecutor
from spx_bot.brokers.ibkr_broker import IBKRConnection, IBKRMarketData, IBKROrderExecutor
from spx_bot.market_data import MarketDataProvider
from spx_bot.orders import OrderExecutor
from spx_bot.position_manager import PositionManager
from spx_bot.risk_manager import RiskManager
from spx_bot.strategies.credit_spreads import CreditSpreadStrategy
from spx_bot.utils.logger import setup_logging, TradeLogger
from spx_bot.utils.alerts import AlertManager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Scan interval in seconds
SCAN_INTERVAL = 30
MONITOR_INTERVAL = 10


class TradingEngine:
    """Main 0DTE trading engine."""

    def __init__(self, cfg: AppConfig = config):
        self.cfg = cfg
        self.running = False

        # Initialize components
        self.market_data: MarketDataProvider
        self.executor: OrderExecutor
        self._init_broker()

        self.risk_manager = RiskManager(cfg)
        self.strategy = CreditSpreadStrategy(cfg)
        self.position_manager = PositionManager(
            executor=self.executor,
            market_data=self.market_data,
            risk_manager=self.risk_manager,
            cfg=cfg,
        )
        self.trade_logger = TradeLogger(cfg.logging.log_dir)
        self.alerts = AlertManager(cfg)

        # Graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _init_broker(self):
        """Initialize the appropriate broker connection."""
        if self.cfg.broker.broker == BrokerType.PAPER:
            md = PaperMarketData()
            self.market_data = md
            self.executor = PaperOrderExecutor(md)
        elif self.cfg.broker.broker == BrokerType.IBKR:
            conn = IBKRConnection(
                host=self.cfg.broker.ibkr_host,
                port=self.cfg.broker.ibkr_port,
                client_id=self.cfg.broker.ibkr_client_id,
            )
            md = IBKRMarketData(conn)
            self.market_data = md
            self.executor = IBKROrderExecutor(conn)
        elif self.cfg.broker.broker == BrokerType.SCHWAB:
            raise NotImplementedError(
                "Schwab broker not yet implemented. Set BROKER=paper for paper trading."
            )
        elif self.cfg.broker.broker == BrokerType.TASTYTRADE:
            auth = TastytradeAuth(
                username=self.cfg.broker.tastytrade_username,
                password=self.cfg.broker.tastytrade_password,
            )
            md = TastytradeMarketData(auth)
            self.market_data = md
            self.executor = TastytradeOrderExecutor(auth)
        else:
            raise ValueError(f"Unknown broker: {self.cfg.broker.broker}")

    def run(self):
        """Main entry point - start the trading loop."""
        setup_logging(self.cfg.logging)

        logger.info("=" * 60)
        logger.info("SPX 0DTE Trading Bot Starting")
        logger.info("=" * 60)
        logger.info("Account: $%.2f", self.cfg.account.starting_capital)
        logger.info("Daily target: $%.2f (%.0f%%)",
                     self.cfg.account.daily_return_target,
                     self.cfg.account.daily_return_target_pct)
        logger.info("Max drawdown: $%.2f (%.0f%%)",
                     self.cfg.account.max_daily_drawdown,
                     self.cfg.account.max_daily_drawdown_pct)
        logger.info("Max trades/day: %d", self.cfg.account.max_trades_per_day)
        logger.info("Max per trade: $%.2f", self.cfg.account.max_per_trade)
        logger.info("Broker: %s", self.cfg.broker.broker.value)
        logger.info("=" * 60)

        # Connect to market data
        if not self.market_data.connect():
            logger.error("Failed to connect to market data. Exiting.")
            sys.exit(1)

        self.running = True

        # Start web dashboard in background thread
        self._start_dashboard()

        self.alerts.send(
            "Bot Started",
            f"SPX 0DTE bot is live. Capital: ${self.cfg.account.starting_capital:,.0f}"
        )

        try:
            self._trading_loop()
        except Exception as e:
            logger.exception("Fatal error in trading loop: %s", e)
            self.alerts.send("FATAL ERROR", str(e))
            self._emergency_shutdown()
        finally:
            self._cleanup()

    def _trading_loop(self):
        """Core trading loop."""
        last_scan_time = 0
        last_monitor_time = 0

        while self.running:
            now = datetime.now(ET)
            now_ts = time_mod.time()

            # Wait for market open
            if not self.market_data.is_market_open():
                if now.time() < time(9, 30):
                    logger.debug("Waiting for market open...")
                    time_mod.sleep(30)
                    continue
                elif now.time() > time(16, 0):
                    logger.info("Market closed. Shutting down for the day.")
                    self._end_of_day()
                    break

            # Monitor open positions (high frequency)
            if now_ts - last_monitor_time >= MONITOR_INTERVAL:
                if self.position_manager.open_positions:
                    actions = self.position_manager.monitor_positions()
                    for action in actions:
                        self.trade_logger.log_close(action)
                        self.alerts.send(
                            f"Position Closed ({action['reason']})",
                            f"{action['position_id']} P&L: ${action['pnl']:.2f}"
                        )
                last_monitor_time = now_ts

            # Scan for new entries (lower frequency)
            if now_ts - last_scan_time >= SCAN_INTERVAL:
                self._scan_and_execute()
                last_scan_time = now_ts

            # Log status periodically
            self._log_status()

            time_mod.sleep(1)

    def _scan_and_execute(self):
        """Scan for signals and execute if risk allows."""
        # Check if we can trade
        can_trade, reason = self.risk_manager.can_open_trade()
        if not can_trade:
            logger.debug("Cannot trade: %s", reason)
            return

        # Get market data
        try:
            snapshot = self.market_data.get_spx_snapshot()
            chain = self.market_data.get_options_chain()
        except Exception as e:
            logger.error("Market data error: %s", e)
            return

        # Get position sizing from risk manager
        max_size = self.risk_manager.max_position_size()
        if max_size <= 0:
            return

        # Scan for signals
        signals = self.strategy.scan_for_signals(
            chain=chain,
            snapshot=snapshot,
            max_position_size=max_size,
            contracts_calculator=self.risk_manager.calculate_contracts,
        )

        if not signals:
            return

        # Take the highest confidence signal
        best_signal = signals[0]
        logger.info(
            "SIGNAL: %s | Confidence: %.2f | %s",
            best_signal.strategy.value,
            best_signal.confidence,
            best_signal.reason,
        )

        # Final risk check on total exposure
        if best_signal.total_max_loss > self.cfg.account.max_per_trade:
            logger.warning(
                "Signal max loss $%.2f exceeds per-trade cap $%.2f. Reducing contracts.",
                best_signal.total_max_loss,
                self.cfg.account.max_per_trade,
            )
            # Reduce contracts to fit
            while (best_signal.contracts > 0 and
                   best_signal.total_max_loss > self.cfg.account.max_per_trade):
                best_signal.contracts -= 1

            if best_signal.contracts <= 0:
                logger.warning("Cannot size trade within limits. Skipping.")
                return

        # Convert to order and execute
        order = best_signal.to_order()
        logger.info(
            "EXECUTING: %s x%d @ $%.2f credit",
            order.spread_side.value,
            order.quantity,
            order.limit_price,
        )

        try:
            fill = self.executor.submit_order(order)
        except Exception as e:
            logger.error("Order execution failed: %s", e)
            return

        # Record position
        pos = self.position_manager.open_position(
            order=order,
            fill=fill,
            underlying_price=snapshot.price,
        )

        self.trade_logger.log_open(pos, best_signal)
        self.alerts.send(
            f"Trade Opened: {best_signal.strategy.value}",
            (
                f"{pos.spread_side.value} {pos.short_strike}/{pos.long_strike} "
                f"x{pos.quantity} @ ${pos.entry_credit:.2f}\n"
                f"Max profit: ${pos.max_profit:.2f} | Max loss: ${pos.max_loss:.2f}"
            ),
        )

    def _end_of_day(self):
        """End of day cleanup."""
        # Close any remaining positions
        if self.position_manager.open_positions:
            logger.info("Closing %d remaining positions at EOD",
                        len(self.position_manager.open_positions))
            actions = self.position_manager.close_all_positions("EOD_CLOSE")
            for action in actions:
                self.trade_logger.log_close(action)

        # Log daily summary
        summary = self.position_manager.get_summary()
        risk_status = self.risk_manager.get_status()

        logger.info("=" * 60)
        logger.info("END OF DAY SUMMARY")
        logger.info("=" * 60)
        logger.info("Trades: %d", summary["closed_count"])
        logger.info("Total P&L: $%.2f", summary["total_realized_pnl"])
        logger.info("Peak P&L: $%.2f", risk_status["peak_pnl"])
        logger.info("=" * 60)

        self.trade_logger.log_daily_summary(summary, risk_status)
        self.alerts.send(
            "End of Day Summary",
            (
                f"Trades: {summary['closed_count']}\n"
                f"P&L: ${summary['total_realized_pnl']:.2f}\n"
                f"Peak: ${risk_status['peak_pnl']:.2f}"
            ),
        )

    def _log_status(self):
        """Periodic status logging (every 5 minutes)."""
        now = datetime.now(ET)
        if now.minute % 5 == 0 and now.second < 2:
            status = self.risk_manager.get_status()
            logger.info(
                "STATUS | Trades: %d/%d | P&L: $%.2f | Unrealized: $%.2f | Open: %d",
                status["trades_taken"],
                self.cfg.account.max_trades_per_day,
                status["realized_pnl"],
                status["unrealized_pnl"],
                len(self.position_manager.open_positions),
            )

    def _start_dashboard(self):
        """Start the web dashboard in a background thread."""
        try:
            from spx_bot.dashboard import init_dashboard, run_dashboard
            init_dashboard(
                engine=self,
                position_manager=self.position_manager,
                risk_manager=self.risk_manager,
            )
            dashboard_thread = threading.Thread(
                target=run_dashboard,
                kwargs={"host": "0.0.0.0", "port": 5555},
                daemon=True,
            )
            dashboard_thread.start()
            logger.info("Dashboard started at http://localhost:5555")
        except Exception as e:
            logger.warning("Dashboard failed to start: %s", e)

    def _emergency_shutdown(self):
        """Close everything immediately."""
        logger.warning("EMERGENCY SHUTDOWN - closing all positions")
        try:
            self.position_manager.close_all_positions("EMERGENCY")
        except Exception as e:
            logger.error("Error during emergency shutdown: %s", e)

    def _cleanup(self):
        """Clean shutdown."""
        self.running = False
        try:
            self.market_data.disconnect()
        except Exception:
            pass
        logger.info("Trading engine shut down.")

    def _shutdown_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM."""
        logger.info("Shutdown signal received (%s)", signum)
        self.running = False
