#!/usr/bin/env python3
"""IBKR Trade Tracker - CLI Entry Point.

Usage:
    python tracker.py                      # Launch dashboard + EOD scheduler
    python tracker.py --pull               # Pull today's trades now
    python tracker.py --dashboard          # Dashboard only (no IBKR connection)
    python tracker.py --stats              # Print today's stats to terminal
    python tracker.py --stats --period weekly   # Print weekly stats
    python tracker.py --port 8051          # Dashboard on custom port
"""

import argparse
import logging
import os
import sys
from datetime import date, time as dt_time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tracker")


def main():
    parser = argparse.ArgumentParser(description="IBKR Trade Tracker")
    parser.add_argument(
        "--pull", action="store_true",
        help="Pull today's trades from IBKR immediately",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Launch dashboard only (no IBKR connection required)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print performance stats to terminal",
    )
    parser.add_argument(
        "--period", choices=["daily", "weekly", "monthly", "ytd"],
        default="daily",
        help="Time period for --stats (default: daily)",
    )
    parser.add_argument(
        "--port", type=int, default=8050,
        help="Port for the dashboard web server (default: 8050)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host for dashboard (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--db", default="data/trades.db",
        help="Path to SQLite database (default: data/trades.db)",
    )
    parser.add_argument(
        "--ibkr-host", default=os.getenv("IBKR_HOST", "127.0.0.1"),
        help="IBKR TWS/Gateway host",
    )
    parser.add_argument(
        "--ibkr-port", type=int,
        default=int(os.getenv("IBKR_PORT", "7497")),
        help="IBKR TWS/Gateway port (7497=TWS paper, 7496=TWS live, "
             "4002=Gateway paper, 4001=Gateway live)",
    )
    parser.add_argument(
        "--ibkr-client-id", type=int,
        default=int(os.getenv("IBKR_TRACKER_CLIENT_ID", "10")),
        help="IBKR client ID for tracker (default: 10)",
    )
    parser.add_argument(
        "--account", default=os.getenv("IBKR_ACCOUNT", ""),
        help="IBKR account ID filter (empty = all accounts)",
    )
    parser.add_argument(
        "--pull-time", default="16:15",
        help="Time to auto-pull trades (HH:MM ET, default: 16:15)",
    )

    args = parser.parse_args()
    db_path = Path(args.db)

    if args.stats:
        _print_stats(db_path, args.period)
        return

    if args.pull:
        _pull_trades(args, db_path)
        return

    if args.dashboard:
        _run_dashboard_only(db_path, args.host, args.port)
        return

    # Default: full mode - scheduler + dashboard
    _run_full(args, db_path)


def _pull_trades(args, db_path: Path):
    """One-shot trade pull from IBKR."""
    from trade_tracker.database import TradeDatabase
    from trade_tracker.ibkr_client import IBKRClient

    client = IBKRClient(
        host=args.ibkr_host,
        port=args.ibkr_port,
        client_id=args.ibkr_client_id,
    )

    if not client.connect():
        logger.error("Could not connect to IBKR. Is TWS/Gateway running?")
        sys.exit(1)

    try:
        db = TradeDatabase(db_path)
        executions = client.fetch_today_executions(account=args.account)
        logger.info("Fetched %d executions", len(executions))

        if executions:
            db.store_executions(executions)
            new_trades = db.match_trades()
            logger.info("Matched %d new round-trip trades", len(new_trades))

            for t in new_trades:
                result = "WIN" if t.pnl > 0 else "LOSS" if t.pnl < 0 else "SCRATCH"
                print(f"  {t.symbol:20s} {t.side.value:4s} "
                      f"qty={t.quantity:.0f}  P&L=${t.pnl:>8,.2f}  [{result}]")
        else:
            print("No executions found for today.")

        db.close()
    finally:
        client.disconnect()


def _print_stats(db_path: Path, period: str):
    """Print stats to terminal."""
    from trade_tracker.analytics import PerformanceAnalytics
    from trade_tracker.database import TradeDatabase

    db = TradeDatabase(db_path)
    analytics = PerformanceAnalytics(db)

    today = date.today()

    if period == "daily":
        stats = analytics.daily_stats(today)
        label = f"Daily Stats - {today}"
    elif period == "weekly":
        stats = analytics.weekly_stats()
        label = "This Week"
    elif period == "monthly":
        stats = analytics.monthly_stats(today.year, today.month)
        label = f"{today.strftime('%B %Y')}"
    else:
        stats = analytics.ytd_stats()
        label = f"YTD {today.year}"

    analytics.print_summary(stats, label)
    db.close()


def _run_dashboard_only(db_path: Path, host: str, port: int):
    """Run just the dashboard without IBKR connection."""
    from trade_tracker.dashboard import create_app

    print(f"\nStarting IBKR Trade Tracker Dashboard")
    print(f"Open http://{host}:{port} in your browser\n")

    app = create_app(db_path)
    app.run(host=host, port=port, debug=False)


def _run_full(args, db_path: Path):
    """Run dashboard + EOD auto-pull scheduler."""
    from trade_tracker.dashboard import create_app
    from trade_tracker.database import TradeDatabase
    from trade_tracker.ibkr_client import IBKRClient
    from trade_tracker.scheduler import EndOfDayScheduler

    # Parse pull time
    h, m = args.pull_time.split(":")
    pull_time = dt_time(int(h), int(m))

    client = IBKRClient(
        host=args.ibkr_host,
        port=args.ibkr_port,
        client_id=args.ibkr_client_id,
    )

    db = TradeDatabase(db_path)

    # Start EOD scheduler
    scheduler = EndOfDayScheduler(
        ibkr_client=client,
        db=db,
        pull_time=pull_time,
        account=args.account,
    )
    scheduler.start()

    print(f"\nIBKR Trade Tracker")
    print(f"  Dashboard:   http://{args.host}:{args.port}")
    print(f"  IBKR:        {args.ibkr_host}:{args.ibkr_port}")
    print(f"  Auto-pull:   {pull_time.strftime('%H:%M')} ET daily")
    print(f"  Database:    {db_path}")
    print(f"\nPress Ctrl+C to stop.\n")

    try:
        app = create_app(db_path)
        app.run(host=args.host, port=args.port, debug=False)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        scheduler.stop()
        client.disconnect()
        db.close()


if __name__ == "__main__":
    main()
