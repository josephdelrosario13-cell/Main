#!/usr/bin/env python3
"""
SPX 0DTE Trading Bot - Entry Point

Usage:
    python main.py                  # Run live/paper trading
    python main.py --backtest       # Run backtester
    python main.py --backtest -d 30 # Backtest 30 days
    python main.py --status         # Show config and risk params
    python main.py --dashboard      # Run dashboard only (no trading)
"""

import argparse
import sys

from spx_bot.config import config


def main():
    parser = argparse.ArgumentParser(description="SPX 0DTE Trading Bot")
    parser.add_argument(
        "--backtest", action="store_true",
        help="Run backtest simulation instead of live trading",
    )
    parser.add_argument(
        "-d", "--days", type=int, default=20,
        help="Number of days to backtest (default: 20)",
    )
    parser.add_argument(
        "--price", type=float, default=5800.0,
        help="Starting SPX price for backtest (default: 5800)",
    )
    parser.add_argument(
        "--vix", type=float, default=18.0,
        help="Starting VIX for backtest (default: 18)",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print current configuration and exit",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Run the web dashboard only (no trading)",
    )
    parser.add_argument(
        "--port", type=int, default=5555,
        help="Dashboard port (default: 5555)",
    )

    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    if args.dashboard:
        _run_dashboard(args)
        return

    if args.backtest:
        _run_backtest(args)
    else:
        _run_live()


def _run_live():
    """Start the live trading engine."""
    from spx_bot.engine import TradingEngine

    print(f"\nStarting SPX 0DTE Trading Bot")
    print(f"Broker: {config.broker.broker.value}")
    print(f"Capital: ${config.account.starting_capital:,.0f}")
    print()

    engine = TradingEngine(config)
    engine.run()


def _run_backtest(args):
    """Run the backtester."""
    from spx_bot.backtester import Backtester

    print(f"\nRunning backtest: {args.days} days")
    print(f"Starting SPX: {args.price}, VIX: {args.vix}")
    print()

    backtester = Backtester(config)
    result = backtester.run(
        num_days=args.days,
        base_price=args.price,
        base_vix=args.vix,
    )

    result.print_report()
    backtester.export_results(result)


def _run_dashboard(args):
    """Run the web dashboard standalone."""
    from spx_bot.dashboard import run_dashboard

    print(f"\nStarting SPX 0DTE Dashboard on port {args.port}")
    print(f"Open http://localhost:{args.port} in your browser")
    print()
    run_dashboard(port=args.port, debug=True)


def _print_status():
    """Print current configuration."""
    print("\n" + "=" * 50)
    print("SPX 0DTE Bot Configuration")
    print("=" * 50)
    print(f"\nAccount:")
    print(f"  Capital:          ${config.account.starting_capital:,.0f}")
    print(f"  Daily target:     {config.account.daily_return_target_pct}% "
          f"(${config.account.daily_return_target:,.0f})")
    print(f"  Max drawdown:     {config.account.max_daily_drawdown_pct}% "
          f"(${config.account.max_daily_drawdown:,.0f})")
    print(f"  Max trades/day:   {config.account.max_trades_per_day}")
    print(f"  Max per trade:    ${config.account.max_per_trade:,.0f}")

    print(f"\nStrategy:")
    print(f"  Spread width:     {config.strategy.spread_width} pts")
    print(f"  Short put delta:  {config.strategy.short_put_delta}")
    print(f"  Short call delta: {config.strategy.short_call_delta}")
    print(f"  Min credit:       {config.strategy.min_credit_pct:.0%} of width")
    print(f"  Profit target:    {config.strategy.profit_target_pct}%")
    print(f"  Stop loss:        {config.strategy.stop_loss_multiple}x credit")
    print(f"  Entry window:     {config.strategy.earliest_entry_time} - "
          f"{config.strategy.latest_entry_time} ET")
    print(f"  Force close:      {config.strategy.force_close_time} ET")
    print(f"  VIX range:        {config.strategy.min_vix} - {config.strategy.max_vix}")

    print(f"\nBroker:")
    print(f"  Type:             {config.broker.broker.value}")

    print(f"\nEnabled strategies:")
    for s in config.strategy.enabled_strategies:
        print(f"  - {s.value}")
    print()


if __name__ == "__main__":
    main()
