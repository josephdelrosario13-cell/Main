"""
Web dashboard for monitoring the SPX 0DTE trading bot.

Provides real-time views of:
- Current P&L and account status
- Open and closed positions
- Trade history from CSV logs
- Risk manager status
- Bot configuration

Run standalone: python -m spx_bot.dashboard
"""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template_string

from spx_bot.config import config

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global references set by the engine when starting the dashboard
_position_manager = None
_risk_manager = None
_engine = None

LOG_DIR = Path(config.logging.log_dir)


def init_dashboard(engine=None, position_manager=None, risk_manager=None):
    """Initialize dashboard with references to live engine components."""
    global _engine, _position_manager, _risk_manager
    _engine = engine
    _position_manager = position_manager
    _risk_manager = risk_manager


# ─── HTML Template ────────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SPX 0DTE Trading Bot</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            background: #0a0a0f;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 20px 30px;
            border-bottom: 1px solid #2a2a4a;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            font-size: 1.4rem;
            color: #00d4ff;
            letter-spacing: 1px;
        }
        .header .status {
            display: flex;
            gap: 20px;
            align-items: center;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 6px;
            animation: pulse 2s infinite;
        }
        .status-dot.live { background: #00ff88; }
        .status-dot.paper { background: #ffaa00; }
        .status-dot.off { background: #ff4444; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: #12121f;
            border: 1px solid #2a2a4a;
            border-radius: 8px;
            padding: 20px;
        }
        .card h2 {
            font-size: 0.85rem;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 15px;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #1a1a2e;
        }
        .metric:last-child { border-bottom: none; }
        .metric .label { color: #999; }
        .metric .value { font-weight: 600; }
        .positive { color: #00ff88; }
        .negative { color: #ff4444; }
        .neutral { color: #ffaa00; }
        .big-number {
            font-size: 2.5rem;
            font-weight: 700;
            margin: 10px 0;
        }
        .big-label {
            font-size: 0.75rem;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }
        th {
            text-align: left;
            padding: 10px 8px;
            color: #666;
            text-transform: uppercase;
            font-size: 0.7rem;
            letter-spacing: 1px;
            border-bottom: 1px solid #2a2a4a;
        }
        td {
            padding: 10px 8px;
            border-bottom: 1px solid #1a1a2e;
        }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.7rem;
            font-weight: 600;
        }
        .badge-open { background: #00ff8822; color: #00ff88; }
        .badge-closed { background: #66666622; color: #999; }
        .badge-profit { background: #00ff8822; color: #00ff88; }
        .badge-loss { background: #ff444422; color: #ff4444; }
        .progress-bar {
            height: 6px;
            background: #1a1a2e;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 5px;
        }
        .progress-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.5s;
        }
        .refresh-btn {
            background: #1a1a2e;
            border: 1px solid #2a2a4a;
            color: #00d4ff;
            padding: 6px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
            font-size: 0.8rem;
        }
        .refresh-btn:hover { background: #2a2a4a; }
        .footer {
            text-align: center;
            padding: 20px;
            color: #444;
            font-size: 0.75rem;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>SPX 0DTE BOT</h1>
        <div class="status">
            <span>
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">Loading...</span>
            </span>
            <span id="clock" style="color: #666;"></span>
            <button class="refresh-btn" onclick="refresh()">Refresh</button>
        </div>
    </div>

    <div class="container">
        <!-- P&L Summary -->
        <div class="grid">
            <div class="card">
                <h2>Daily P&L</h2>
                <div class="big-number" id="dailyPnl">$0.00</div>
                <div class="big-label">Realized Today</div>
                <div class="progress-bar" style="margin-top: 15px;">
                    <div class="progress-fill" id="pnlProgress"
                         style="width: 0%; background: #00ff88;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; margin-top: 5px; font-size: 0.7rem; color: #666;">
                    <span>-${{ max_dd }}</span>
                    <span>Target: ${{ target }}</span>
                </div>
            </div>

            <div class="card">
                <h2>Risk Status</h2>
                <div class="metric">
                    <span class="label">Trades Today</span>
                    <span class="value" id="tradeCount">0 / {{ max_trades }}</span>
                </div>
                <div class="metric">
                    <span class="label">Peak P&L</span>
                    <span class="value" id="peakPnl">$0.00</span>
                </div>
                <div class="metric">
                    <span class="label">Unrealized</span>
                    <span class="value" id="unrealizedPnl">$0.00</span>
                </div>
                <div class="metric">
                    <span class="label">Max Position Size</span>
                    <span class="value" id="maxPosSize">$0.00</span>
                </div>
                <div class="metric">
                    <span class="label">Drawdown Hit</span>
                    <span class="value" id="ddHit">No</span>
                </div>
                <div class="metric">
                    <span class="label">Target Hit</span>
                    <span class="value" id="targetHit">No</span>
                </div>
            </div>

            <div class="card">
                <h2>Configuration</h2>
                <div class="metric">
                    <span class="label">Capital</span>
                    <span class="value">${{ capital }}</span>
                </div>
                <div class="metric">
                    <span class="label">Broker</span>
                    <span class="value">{{ broker }}</span>
                </div>
                <div class="metric">
                    <span class="label">Spread Width</span>
                    <span class="value">{{ spread_width }} pts</span>
                </div>
                <div class="metric">
                    <span class="label">Put Delta</span>
                    <span class="value">{{ put_delta }}</span>
                </div>
                <div class="metric">
                    <span class="label">Call Delta</span>
                    <span class="value">{{ call_delta }}</span>
                </div>
                <div class="metric">
                    <span class="label">Entry Window</span>
                    <span class="value">{{ entry_start }}-{{ entry_end }} ET</span>
                </div>
            </div>
        </div>

        <!-- Open Positions -->
        <div class="card" style="margin-bottom: 20px;">
            <h2>Open Positions</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Side</th>
                        <th>Strikes</th>
                        <th>Qty</th>
                        <th>Entry Credit</th>
                        <th>Max Profit</th>
                        <th>Max Loss</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="openPositions">
                    <tr><td colspan="8" style="text-align: center; color: #666;">No open positions</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Trade History -->
        <div class="card">
            <h2>Trade History (Today)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>ID</th>
                        <th>Action</th>
                        <th>Strategy</th>
                        <th>Side</th>
                        <th>Strikes</th>
                        <th>Qty</th>
                        <th>Price</th>
                        <th>P&L</th>
                        <th>Reason</th>
                    </tr>
                </thead>
                <tbody id="tradeHistory">
                    <tr><td colspan="10" style="text-align: center; color: #666;">No trades today</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="footer">
        SPX 0DTE Trading Bot | Auto-refreshes every 10s
    </div>

    <script>
        function refresh() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    // Update status
                    const dot = document.getElementById('statusDot');
                    const text = document.getElementById('statusText');
                    if (data.running) {
                        dot.className = 'status-dot ' + (data.broker === 'paper' ? 'paper' : 'live');
                        text.textContent = data.broker === 'paper' ? 'Paper Trading' : 'LIVE';
                    } else {
                        dot.className = 'status-dot off';
                        text.textContent = 'Offline';
                    }

                    // P&L
                    const pnl = data.risk.realized_pnl || 0;
                    const pnlEl = document.getElementById('dailyPnl');
                    pnlEl.textContent = '$' + pnl.toFixed(2);
                    pnlEl.className = 'big-number ' + (pnl >= 0 ? 'positive' : 'negative');

                    // Progress bar
                    const maxDD = {{ max_dd_raw }};
                    const target = {{ target_raw }};
                    const pct = Math.min(100, Math.max(0, ((pnl + maxDD) / (target + maxDD)) * 100));
                    const bar = document.getElementById('pnlProgress');
                    bar.style.width = pct + '%';
                    bar.style.background = pnl >= 0 ? '#00ff88' : '#ff4444';

                    // Risk
                    document.getElementById('tradeCount').textContent =
                        data.risk.trades_taken + ' / {{ max_trades }}';
                    document.getElementById('peakPnl').textContent =
                        '$' + (data.risk.peak_pnl || 0).toFixed(2);
                    const unrealized = data.risk.unrealized_pnl || 0;
                    const unrealEl = document.getElementById('unrealizedPnl');
                    unrealEl.textContent = '$' + unrealized.toFixed(2);
                    unrealEl.className = 'value ' + (unrealized >= 0 ? 'positive' : 'negative');
                    document.getElementById('maxPosSize').textContent =
                        '$' + (data.risk.max_position_size || 0).toFixed(2);
                    document.getElementById('ddHit').textContent =
                        data.risk.max_drawdown_hit ? 'YES' : 'No';
                    document.getElementById('targetHit').textContent =
                        data.risk.target_hit ? 'YES' : 'No';

                    // Open positions
                    const tbody = document.getElementById('openPositions');
                    if (data.positions.open.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#666;">No open positions</td></tr>';
                    } else {
                        tbody.innerHTML = data.positions.open.map(p => `
                            <tr>
                                <td>${p.id}</td>
                                <td>${p.side}</td>
                                <td>${p.strikes}</td>
                                <td>${p.qty}</td>
                                <td>$${p.entry.toFixed(2)}</td>
                                <td class="positive">$${(p.entry * 100 * p.qty).toFixed(2)}</td>
                                <td class="negative">$${((p.width - p.entry) * 100 * p.qty).toFixed(2)}</td>
                                <td><span class="badge badge-open">OPEN</span></td>
                            </tr>
                        `).join('');
                    }

                    // Trade history
                    const histBody = document.getElementById('tradeHistory');
                    if (data.trades.length === 0) {
                        histBody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#666;">No trades today</td></tr>';
                    } else {
                        histBody.innerHTML = data.trades.map(t => `
                            <tr>
                                <td>${t.time}</td>
                                <td>${t.id}</td>
                                <td>${t.action}</td>
                                <td>${t.strategy}</td>
                                <td>${t.side}</td>
                                <td>${t.strikes}</td>
                                <td>${t.qty}</td>
                                <td>$${t.price}</td>
                                <td class="${parseFloat(t.pnl || 0) >= 0 ? 'positive' : 'negative'}">${t.pnl ? '$' + t.pnl : '-'}</td>
                                <td>${t.reason}</td>
                            </tr>
                        `).join('');
                    }
                })
                .catch(e => console.error('Refresh failed:', e));
        }

        // Clock
        function updateClock() {
            const now = new Date();
            document.getElementById('clock').textContent =
                now.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false }) + ' ET';
        }

        setInterval(refresh, 10000);
        setInterval(updateClock, 1000);
        updateClock();
        refresh();
    </script>
</body>
</html>
"""


# ─── Routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Render the main dashboard."""
    return render_template_string(
        DASHBOARD_HTML,
        capital=f"{config.account.starting_capital:,.0f}",
        broker=config.broker.broker.value,
        spread_width=config.strategy.spread_width,
        put_delta=config.strategy.short_put_delta,
        call_delta=config.strategy.short_call_delta,
        entry_start=config.strategy.earliest_entry_time,
        entry_end=config.strategy.latest_entry_time,
        max_trades=config.account.max_trades_per_day,
        target=f"{config.account.daily_return_target:,.0f}",
        max_dd=f"{config.account.max_daily_drawdown:,.0f}",
        target_raw=config.account.daily_return_target,
        max_dd_raw=config.account.max_daily_drawdown,
    )


@app.route("/api/status")
def api_status():
    """Return current bot status as JSON."""
    risk_status = {}
    positions_data = {"open": [], "closed": []}
    running = False
    broker = config.broker.broker.value

    if _risk_manager:
        risk_status = _risk_manager.get_status()

    if _position_manager:
        summary = _position_manager.get_summary()
        positions_data = {
            "open": [
                {
                    "id": p["id"],
                    "side": p["side"],
                    "strikes": p["strikes"],
                    "qty": p["qty"],
                    "entry": p["entry"],
                    "width": config.strategy.spread_width,
                }
                for p in summary["positions"] if p["open"]
            ],
            "closed": [
                {
                    "id": p["id"],
                    "side": p["side"],
                    "strikes": p["strikes"],
                    "qty": p["qty"],
                    "entry": p["entry"],
                    "exit": p["exit"],
                    "pnl": p["pnl"],
                }
                for p in summary["positions"] if not p["open"]
            ],
        }

    if _engine:
        running = _engine.running

    # Read today's trades from CSV
    trades = _read_todays_trades()

    return jsonify({
        "running": running,
        "broker": broker,
        "risk": risk_status,
        "positions": positions_data,
        "trades": trades,
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/history")
def api_history():
    """Return historical daily summaries."""
    summaries = []
    daily_file = LOG_DIR / "daily_summary.csv"

    if daily_file.exists():
        with open(daily_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                summaries.append(row)

    return jsonify({"days": summaries[-30:]})  # Last 30 days


@app.route("/api/backtest")
def api_backtest():
    """Return latest backtest results if available."""
    bt_file = Path("data/backtest_results.csv")
    if not bt_file.exists():
        return jsonify({"error": "No backtest results found"})

    results = []
    with open(bt_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            results.append(row)

    return jsonify({"results": results})


def _read_todays_trades() -> list[dict]:
    """Read today's trades from the CSV log."""
    trade_file = LOG_DIR / "trades.csv"
    if not trade_file.exists():
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    trades = []

    try:
        with open(trade_file) as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            if not headers:
                return []

            for row in reader:
                if not row or not row[0].startswith(today):
                    continue
                trades.append({
                    "time": row[0].split("T")[1][:8] if "T" in row[0] else row[0],
                    "id": row[1] if len(row) > 1 else "",
                    "action": row[2] if len(row) > 2 else "",
                    "strategy": row[3] if len(row) > 3 else "",
                    "side": row[4] if len(row) > 4 else "",
                    "strikes": f"{row[5]}/{row[6]}" if len(row) > 6 and row[5] else "",
                    "qty": row[7] if len(row) > 7 else "",
                    "price": row[8] if len(row) > 8 else "",
                    "pnl": row[9] if len(row) > 9 else "",
                    "reason": row[10] if len(row) > 10 else "",
                })
    except Exception as e:
        logger.error("Error reading trade log: %s", e)

    return trades


def run_dashboard(host: str = "0.0.0.0", port: int = 5555, debug: bool = False):
    """Start the dashboard web server."""
    logger.info("Starting dashboard on %s:%d", host, port)
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_dashboard(debug=True)
