"""SQLite database for persistent trade storage."""

import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from trade_tracker.models import AssetClass, DailyStats, Execution, Side, Trade

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

DEFAULT_DB_PATH = Path("data/trades.db")


class TradeDatabase:
    """SQLite-backed trade storage with round-trip matching."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS executions (
                exec_id TEXT PRIMARY KEY,
                order_id INTEGER,
                account TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                timestamp TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                exchange TEXT,
                commission REAL DEFAULT 0,
                realized_pnl REAL
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                side TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                open_price REAL NOT NULL,
                close_price REAL NOT NULL,
                quantity REAL NOT NULL,
                commission REAL DEFAULT 0,
                pnl REAL NOT NULL,
                account TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_trades_close_time
                ON trades(close_time);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol
                ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_executions_timestamp
                ON executions(timestamp);
        """)
        self.conn.commit()

    def store_executions(self, executions: list[Execution]) -> int:
        """Store executions, skipping duplicates. Returns count of new rows."""
        inserted = 0
        for ex in executions:
            try:
                self.conn.execute(
                    """INSERT OR IGNORE INTO executions
                       (exec_id, order_id, account, symbol, side, quantity,
                        price, timestamp, asset_class, exchange, commission,
                        realized_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ex.exec_id, ex.order_id, ex.account, ex.symbol,
                        ex.side.value, ex.quantity, ex.price,
                        ex.timestamp.isoformat(), ex.asset_class.value,
                        ex.exchange, ex.commission, ex.realized_pnl,
                    ),
                )
                if self.conn.total_changes:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        logger.info("Stored %d new executions", inserted)
        return inserted

    def match_trades(self) -> list[Trade]:
        """Match buy/sell executions into round-trip trades using FIFO.

        Groups executions by symbol, then pairs opposing sides chronologically.
        Stores matched trades and returns them.
        """
        rows = self.conn.execute(
            """SELECT * FROM executions
               WHERE exec_id NOT IN (
                   SELECT exec_id FROM _matched_execs
               )
               ORDER BY timestamp""",
        ).fetchall()

        if not rows:
            # If _matched_execs doesn't exist yet, fetch all and create it
            pass

        # Ensure tracking table exists
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS _matched_execs (
                exec_id TEXT PRIMARY KEY
            )
        """)

        # Get all unmatched executions
        rows = self.conn.execute(
            """SELECT * FROM executions
               WHERE exec_id NOT IN (SELECT exec_id FROM _matched_execs)
               ORDER BY timestamp""",
        ).fetchall()

        # Group by symbol
        by_symbol: dict[str, list[dict]] = {}
        for row in rows:
            symbol = row["symbol"]
            by_symbol.setdefault(symbol, []).append(dict(row))

        new_trades = []
        matched_exec_ids = []

        for symbol, execs in by_symbol.items():
            buys = [e for e in execs if e["side"] == "BUY"]
            sells = [e for e in execs if e["side"] == "SELL"]

            # FIFO matching
            bi, si = 0, 0
            while bi < len(buys) and si < len(sells):
                buy = buys[bi]
                sell = sells[si]

                qty = min(buy["quantity"], sell["quantity"])

                buy_time = datetime.fromisoformat(buy["timestamp"])
                sell_time = datetime.fromisoformat(sell["timestamp"])

                # Determine which is open vs close
                if buy_time <= sell_time:
                    open_exec, close_exec = buy, sell
                    opening_side = Side.BUY
                    pnl_per_unit = sell["price"] - buy["price"]
                else:
                    open_exec, close_exec = sell, buy
                    opening_side = Side.SELL
                    pnl_per_unit = sell["price"] - buy["price"]

                open_time = datetime.fromisoformat(open_exec["timestamp"])
                close_time = datetime.fromisoformat(close_exec["timestamp"])

                total_commission = (
                    (buy.get("commission", 0) or 0)
                    + (sell.get("commission", 0) or 0)
                ) * (qty / max(buy["quantity"], sell["quantity"]))

                gross_pnl = pnl_per_unit * qty
                # For options, multiply by contract multiplier (100)
                if open_exec["asset_class"] == "OPT":
                    gross_pnl *= 100

                net_pnl = gross_pnl - total_commission

                trade_id = f"{open_exec['exec_id']}_{close_exec['exec_id']}"

                trade = Trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    asset_class=AssetClass(open_exec["asset_class"]),
                    side=opening_side,
                    open_time=open_time,
                    close_time=close_time,
                    open_price=open_exec["price"],
                    close_price=close_exec["price"],
                    quantity=qty,
                    commission=total_commission,
                    pnl=round(net_pnl, 2),
                    account=open_exec.get("account", ""),
                )

                new_trades.append(trade)
                matched_exec_ids.extend([buy["exec_id"], sell["exec_id"]])

                # Reduce quantities
                buys[bi]["quantity"] -= qty
                sells[si]["quantity"] -= qty
                if buys[bi]["quantity"] <= 0:
                    bi += 1
                if sells[si]["quantity"] <= 0:
                    si += 1

        # Store matched trades
        for trade in new_trades:
            self.conn.execute(
                """INSERT OR IGNORE INTO trades
                   (trade_id, symbol, asset_class, side, open_time, close_time,
                    open_price, close_price, quantity, commission, pnl, account)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.trade_id, trade.symbol, trade.asset_class.value,
                    trade.side.value, trade.open_time.isoformat(),
                    trade.close_time.isoformat(), trade.open_price,
                    trade.close_price, trade.quantity, trade.commission,
                    trade.pnl, trade.account,
                ),
            )

        # Mark executions as matched
        for exec_id in matched_exec_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO _matched_execs (exec_id) VALUES (?)",
                (exec_id,),
            )

        self.conn.commit()
        logger.info("Matched %d new round-trip trades", len(new_trades))
        return new_trades

    def get_trades(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        symbol: Optional[str] = None,
    ) -> list[Trade]:
        """Retrieve trades with optional date/symbol filters."""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list = []

        if start_date:
            query += " AND close_time >= ?"
            params.append(
                datetime(start_date.year, start_date.month, start_date.day,
                         tzinfo=ET).isoformat()
            )
        if end_date:
            query += " AND close_time < ?"
            next_day = datetime(end_date.year, end_date.month, end_date.day,
                                tzinfo=ET) + timedelta(days=1)
            params.append(next_day.isoformat())
        if symbol:
            query += " AND symbol LIKE ?"
            params.append(f"%{symbol}%")

        query += " ORDER BY close_time"
        rows = self.conn.execute(query, params).fetchall()

        trades = []
        for row in rows:
            trades.append(Trade(
                trade_id=row["trade_id"],
                symbol=row["symbol"],
                asset_class=AssetClass(row["asset_class"]),
                side=Side(row["side"]),
                open_time=datetime.fromisoformat(row["open_time"]),
                close_time=datetime.fromisoformat(row["close_time"]),
                open_price=row["open_price"],
                close_price=row["close_price"],
                quantity=row["quantity"],
                commission=row["commission"],
                pnl=row["pnl"],
                account=row["account"] or "",
            ))
        return trades

    def get_all_trade_dates(self) -> list[date]:
        """Return sorted list of all dates that have trades."""
        rows = self.conn.execute(
            "SELECT DISTINCT DATE(close_time) as d FROM trades ORDER BY d",
        ).fetchall()
        return [date.fromisoformat(row["d"]) for row in rows]

    def close(self):
        self.conn.close()
