"""
Configuration and risk parameters for the SPX 0DTE trading bot.

All dollar amounts, risk limits, and strategy parameters are centralized here.
Values can be overridden via environment variables or .env file.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class StrategyType(Enum):
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    IRON_CONDOR = "iron_condor"


class BrokerType(Enum):
    IBKR = "ibkr"
    SCHWAB = "schwab"
    TASTYTRADE = "tastytrade"
    PAPER = "paper"


@dataclass(frozen=True)
class AccountConfig:
    """Account and capital parameters."""
    starting_capital: float = float(os.getenv("STARTING_CAPITAL", "5000"))
    daily_return_target_pct: float = float(os.getenv("DAILY_RETURN_TARGET_PCT", "20"))
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "20"))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "4"))
    max_per_trade: float = float(os.getenv("MAX_PER_TRADE", "1200"))

    @property
    def daily_return_target(self) -> float:
        return self.starting_capital * (self.daily_return_target_pct / 100)

    @property
    def max_daily_drawdown(self) -> float:
        return self.starting_capital * (self.max_daily_drawdown_pct / 100)


@dataclass(frozen=True)
class StrategyConfig:
    """Parameters controlling trade entry and exit logic."""
    # Spread width in SPX points (e.g., 5-point wide spreads)
    spread_width: int = int(os.getenv("SPREAD_WIDTH", "5"))

    # Delta targets for short strikes
    short_put_delta: float = float(os.getenv("SHORT_PUT_DELTA", "-0.16"))
    short_call_delta: float = float(os.getenv("SHORT_CALL_DELTA", "0.16"))

    # Minimum credit to collect per spread (as fraction of spread width)
    min_credit_pct: float = float(os.getenv("MIN_CREDIT_PCT", "0.10"))

    # Profit target: close at this % of max profit
    profit_target_pct: float = float(os.getenv("PROFIT_TARGET_PCT", "50"))

    # Stop loss: close if loss exceeds this multiple of credit received
    stop_loss_multiple: float = float(os.getenv("STOP_LOSS_MULTIPLE", "2.0"))

    # Minimum DTE (0 for same-day only)
    max_dte: int = 0

    # Time windows (Eastern Time, 24h format)
    earliest_entry_time: str = os.getenv("EARLIEST_ENTRY_TIME", "09:45")
    latest_entry_time: str = os.getenv("LATEST_ENTRY_TIME", "14:30")
    force_close_time: str = os.getenv("FORCE_CLOSE_TIME", "15:45")

    # VIX-based filters
    min_vix: float = float(os.getenv("MIN_VIX", "12"))
    max_vix: float = float(os.getenv("MAX_VIX", "35"))

    # Enabled strategies
    enabled_strategies: tuple = (
        StrategyType.BULL_PUT_SPREAD,
        StrategyType.BEAR_CALL_SPREAD,
        StrategyType.IRON_CONDOR,
    )


@dataclass(frozen=True)
class BrokerConfig:
    """Broker connection settings."""
    broker: BrokerType = BrokerType(os.getenv("BROKER", "paper"))

    # IBKR TWS/Gateway
    ibkr_host: str = os.getenv("IBKR_HOST", "127.0.0.1")
    ibkr_port: int = int(os.getenv("IBKR_PORT", "7497"))
    ibkr_client_id: int = int(os.getenv("IBKR_CLIENT_ID", "1"))

    # Schwab API
    schwab_app_key: str = os.getenv("SCHWAB_APP_KEY", "")
    schwab_app_secret: str = os.getenv("SCHWAB_APP_SECRET", "")
    schwab_callback_url: str = os.getenv("SCHWAB_CALLBACK_URL", "")
    schwab_token_path: str = os.getenv("SCHWAB_TOKEN_PATH", "")

    # Tastytrade
    tastytrade_username: str = os.getenv("TASTYTRADE_USERNAME", "")
    tastytrade_password: str = os.getenv("TASTYTRADE_PASSWORD", "")


@dataclass(frozen=True)
class LoggingConfig:
    """Logging and output settings."""
    log_dir: str = os.getenv("LOG_DIR", "logs")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    trade_log_file: str = "trades.csv"
    enable_discord_alerts: bool = os.getenv("ENABLE_DISCORD_ALERTS", "false").lower() == "true"
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")


@dataclass
class AppConfig:
    """Top-level configuration aggregating all sub-configs."""
    account: AccountConfig = field(default_factory=AccountConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# Singleton config instance
config = AppConfig()
