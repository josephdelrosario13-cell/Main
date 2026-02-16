"""Interactive web dashboard using Dash + Plotly.

Displays daily, weekly, monthly, and YTD trading performance with
interactive charts and summary statistics.
"""

import logging
from datetime import date, timedelta
from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, callback, dcc, html
from dash.dash_table import DataTable

from trade_tracker.analytics import PerformanceAnalytics
from trade_tracker.database import TradeDatabase
from trade_tracker.models import DailyStats

logger = logging.getLogger(__name__)


def create_app(db_path: Path = Path("data/trades.db")) -> Dash:
    """Create and configure the Dash application."""
    db = TradeDatabase(db_path)
    analytics = PerformanceAnalytics(db)

    app = Dash(
        __name__,
        title="IBKR Trade Tracker",
        suppress_callback_exceptions=True,
    )

    app.layout = html.Div(
        style={"fontFamily": "Segoe UI, Arial, sans-serif",
               "backgroundColor": "#1a1a2e", "color": "#e0e0e0",
               "minHeight": "100vh", "padding": "20px"},
        children=[
            # Header
            html.H1(
                "IBKR Trade Tracker",
                style={"textAlign": "center", "color": "#00d4ff",
                       "marginBottom": "5px"},
            ),
            html.P(
                "Daily performance tracking and analytics",
                style={"textAlign": "center", "color": "#888",
                       "marginBottom": "30px"},
            ),

            # Time period selector
            html.Div(
                style={"textAlign": "center", "marginBottom": "30px"},
                children=[
                    dcc.RadioItems(
                        id="period-selector",
                        options=[
                            {"label": " Daily", "value": "daily"},
                            {"label": " Weekly", "value": "weekly"},
                            {"label": " Monthly", "value": "monthly"},
                            {"label": " YTD", "value": "ytd"},
                        ],
                        value="daily",
                        inline=True,
                        style={"fontSize": "16px"},
                        labelStyle={"marginRight": "30px", "cursor": "pointer"},
                    ),
                ],
            ),

            # KPI cards row
            html.Div(id="kpi-cards", style={
                "display": "flex", "justifyContent": "center",
                "gap": "20px", "marginBottom": "30px", "flexWrap": "wrap",
            }),

            # Charts row 1: Equity curve + Daily P&L bar chart
            html.Div(
                style={"display": "flex", "gap": "20px",
                       "marginBottom": "20px", "flexWrap": "wrap"},
                children=[
                    html.Div(
                        style={"flex": "1", "minWidth": "400px"},
                        children=[dcc.Graph(id="equity-curve")],
                    ),
                    html.Div(
                        style={"flex": "1", "minWidth": "400px"},
                        children=[dcc.Graph(id="pnl-bar-chart")],
                    ),
                ],
            ),

            # Charts row 2: Win rate + Trade count
            html.Div(
                style={"display": "flex", "gap": "20px",
                       "marginBottom": "20px", "flexWrap": "wrap"},
                children=[
                    html.Div(
                        style={"flex": "1", "minWidth": "400px"},
                        children=[dcc.Graph(id="win-loss-chart")],
                    ),
                    html.Div(
                        style={"flex": "1", "minWidth": "400px"},
                        children=[dcc.Graph(id="trade-count-chart")],
                    ),
                ],
            ),

            # Charts row 3: Avg win/loss + Profit factor
            html.Div(
                style={"display": "flex", "gap": "20px",
                       "marginBottom": "20px", "flexWrap": "wrap"},
                children=[
                    html.Div(
                        style={"flex": "1", "minWidth": "400px"},
                        children=[dcc.Graph(id="avg-win-loss-chart")],
                    ),
                    html.Div(
                        style={"flex": "1", "minWidth": "400px"},
                        children=[dcc.Graph(id="profit-factor-chart")],
                    ),
                ],
            ),

            # Trade log table
            html.H3("Trade Log", style={"color": "#00d4ff",
                                         "marginTop": "30px"}),
            html.Div(id="trade-table-container"),

            # Hidden interval for auto-refresh
            dcc.Interval(id="refresh-interval", interval=60_000, n_intervals=0),
        ],
    )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    @app.callback(
        [
            Output("kpi-cards", "children"),
            Output("equity-curve", "figure"),
            Output("pnl-bar-chart", "figure"),
            Output("win-loss-chart", "figure"),
            Output("trade-count-chart", "figure"),
            Output("avg-win-loss-chart", "figure"),
            Output("profit-factor-chart", "figure"),
            Output("trade-table-container", "children"),
        ],
        [
            Input("period-selector", "value"),
            Input("refresh-interval", "n_intervals"),
        ],
    )
    def update_dashboard(period: str, _n: int):
        period_stats = _get_period_stats(analytics, period)
        summary = _get_summary_stats(analytics, period)

        kpi_cards = _build_kpi_cards(summary)
        equity_fig = _build_equity_curve(analytics, period)
        pnl_fig = _build_pnl_bars(period_stats, period)
        win_loss_fig = _build_win_loss_chart(period_stats, period)
        count_fig = _build_trade_count_chart(period_stats, period)
        avg_fig = _build_avg_win_loss_chart(period_stats, period)
        pf_fig = _build_profit_factor_chart(period_stats, period)
        table = _build_trade_table(analytics, period)

        return (
            kpi_cards, equity_fig, pnl_fig, win_loss_fig,
            count_fig, avg_fig, pf_fig, table,
        )

    return app


# ======================================================================
# Helper functions for building chart data
# ======================================================================

_CHART_BG = "#16213e"
_CHART_PAPER = "#1a1a2e"
_GRID_COLOR = "#2a2a4a"
_WIN_COLOR = "#00e676"
_LOSS_COLOR = "#ff5252"
_ACCENT = "#00d4ff"


def _chart_layout(title: str) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=_ACCENT, size=14)),
        paper_bgcolor=_CHART_PAPER,
        plot_bgcolor=_CHART_BG,
        font=dict(color="#ccc"),
        xaxis=dict(gridcolor=_GRID_COLOR),
        yaxis=dict(gridcolor=_GRID_COLOR),
        margin=dict(l=50, r=20, t=40, b=40),
    )


def _get_period_stats(
    analytics: PerformanceAnalytics, period: str,
) -> list[DailyStats]:
    today = date.today()

    if period == "daily":
        start = today - timedelta(days=30)
        return analytics.daily_stats_range(start, today)
    elif period == "weekly":
        return analytics.weekly_stats_range(num_weeks=12)
    elif period == "monthly":
        return analytics.monthly_stats_range(num_months=12)
    else:  # ytd
        start = date(today.year, 1, 1)
        return analytics.daily_stats_range(start, today)


def _get_summary_stats(
    analytics: PerformanceAnalytics, period: str,
) -> DailyStats:
    today = date.today()

    if period == "daily":
        return analytics.daily_stats(today)
    elif period == "weekly":
        return analytics.weekly_stats()
    elif period == "monthly":
        return analytics.monthly_stats(today.year, today.month)
    else:
        return analytics.ytd_stats()


def _kpi_card(label: str, value: str, color: str = "#e0e0e0") -> html.Div:
    return html.Div(
        style={
            "backgroundColor": _CHART_BG, "borderRadius": "10px",
            "padding": "15px 25px", "textAlign": "center",
            "minWidth": "140px",
        },
        children=[
            html.Div(label, style={"fontSize": "12px", "color": "#888",
                                    "marginBottom": "5px"}),
            html.Div(value, style={"fontSize": "22px", "fontWeight": "bold",
                                    "color": color}),
        ],
    )


def _build_kpi_cards(stats: DailyStats) -> list:
    pnl_color = _WIN_COLOR if stats.net_pnl >= 0 else _LOSS_COLOR
    return [
        _kpi_card("Total Trades", str(stats.total_trades)),
        _kpi_card("Net P&L", f"${stats.net_pnl:,.2f}", pnl_color),
        _kpi_card("Win Rate", f"{stats.win_rate:.1%}",
                  _WIN_COLOR if stats.win_rate >= 0.5 else _LOSS_COLOR),
        _kpi_card("Winners", str(stats.winners), _WIN_COLOR),
        _kpi_card("Losers", str(stats.losers), _LOSS_COLOR),
        _kpi_card("Avg Win", f"${stats.avg_win:,.2f}", _WIN_COLOR),
        _kpi_card("Avg Loss", f"${stats.avg_loss:,.2f}", _LOSS_COLOR),
        _kpi_card("Profit Factor", f"{stats.profit_factor:.2f}", _ACCENT),
    ]


def _build_equity_curve(
    analytics: PerformanceAnalytics, period: str,
) -> go.Figure:
    today = date.today()
    if period == "ytd":
        start = date(today.year, 1, 1)
    elif period == "monthly":
        start = date(today.year, today.month, 1)
    elif period == "weekly":
        start = today - timedelta(days=today.weekday())
    else:
        start = today - timedelta(days=30)

    data = analytics.cumulative_pnl(start=start)
    dates = [d for d, _ in data]
    values = [v for _, v in data]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=values, mode="lines+markers",
        line=dict(color=_ACCENT, width=2),
        marker=dict(size=4),
        fill="tozeroy",
        fillcolor="rgba(0,212,255,0.1)",
        name="Cumulative P&L",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="#555")
    fig.update_layout(**_chart_layout("Equity Curve"))
    fig.update_layout(yaxis_title="Cumulative P&L ($)")
    return fig


def _build_pnl_bars(
    stats_list: list[DailyStats], period: str,
) -> go.Figure:
    dates = [s.date for s in stats_list]
    pnls = [s.net_pnl for s in stats_list]
    colors = [_WIN_COLOR if p >= 0 else _LOSS_COLOR for p in pnls]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=pnls, marker_color=colors, name="Net P&L",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="#555")
    label = period.capitalize()
    fig.update_layout(**_chart_layout(f"{label} Net P&L"))
    fig.update_layout(yaxis_title="P&L ($)")
    return fig


def _build_win_loss_chart(
    stats_list: list[DailyStats], period: str,
) -> go.Figure:
    dates = [s.date for s in stats_list]
    win_rates = [s.win_rate * 100 for s in stats_list]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=win_rates, mode="lines+markers",
        line=dict(color=_ACCENT, width=2),
        marker=dict(size=5),
        name="Win Rate %",
    ))
    fig.add_hline(y=50, line_dash="dash", line_color="#888")
    label = period.capitalize()
    fig.update_layout(**_chart_layout(f"{label} Win Rate"))
    fig.update_layout(yaxis_title="Win Rate (%)", yaxis_range=[0, 100])
    return fig


def _build_trade_count_chart(
    stats_list: list[DailyStats], period: str,
) -> go.Figure:
    dates = [s.date for s in stats_list]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=[s.winners for s in stats_list],
        name="Winners", marker_color=_WIN_COLOR,
    ))
    fig.add_trace(go.Bar(
        x=dates, y=[s.losers for s in stats_list],
        name="Losers", marker_color=_LOSS_COLOR,
    ))
    label = period.capitalize()
    fig.update_layout(**_chart_layout(f"{label} Trade Count"))
    fig.update_layout(barmode="stack", yaxis_title="# Trades")
    return fig


def _build_avg_win_loss_chart(
    stats_list: list[DailyStats], period: str,
) -> go.Figure:
    dates = [s.date for s in stats_list]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=[s.avg_win for s in stats_list],
        name="Avg Win", marker_color=_WIN_COLOR,
    ))
    fig.add_trace(go.Bar(
        x=dates, y=[abs(s.avg_loss) for s in stats_list],
        name="Avg Loss", marker_color=_LOSS_COLOR,
    ))
    label = period.capitalize()
    fig.update_layout(**_chart_layout(f"{label} Avg Win vs Avg Loss"))
    fig.update_layout(barmode="group", yaxis_title="Amount ($)")
    return fig


def _build_profit_factor_chart(
    stats_list: list[DailyStats], period: str,
) -> go.Figure:
    dates = [s.date for s in stats_list]
    pfs = [s.profit_factor for s in stats_list]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=pfs, mode="lines+markers",
        line=dict(color="#ffd740", width=2),
        marker=dict(size=5),
        name="Profit Factor",
    ))
    fig.add_hline(y=1.0, line_dash="dash", line_color="#888",
                  annotation_text="Breakeven")
    label = period.capitalize()
    fig.update_layout(**_chart_layout(f"{label} Profit Factor"))
    fig.update_layout(yaxis_title="Profit Factor")
    return fig


def _build_trade_table(
    analytics: PerformanceAnalytics, period: str,
) -> DataTable:
    today = date.today()

    if period == "daily":
        trades = analytics.db.get_trades(start_date=today, end_date=today)
    elif period == "weekly":
        monday = today - timedelta(days=today.weekday())
        trades = analytics.db.get_trades(start_date=monday, end_date=today)
    elif period == "monthly":
        month_start = date(today.year, today.month, 1)
        trades = analytics.db.get_trades(start_date=month_start, end_date=today)
    else:
        ytd_start = date(today.year, 1, 1)
        trades = analytics.db.get_trades(start_date=ytd_start, end_date=today)

    data = []
    for t in trades:
        data.append({
            "Date": t.close_time.strftime("%Y-%m-%d"),
            "Time": t.close_time.strftime("%H:%M:%S"),
            "Symbol": t.symbol,
            "Side": t.side.value,
            "Qty": t.quantity,
            "Open": f"${t.open_price:.2f}",
            "Close": f"${t.close_price:.2f}",
            "P&L": f"${t.pnl:,.2f}",
            "Result": "WIN" if t.pnl > 0 else ("LOSS" if t.pnl < 0 else "SCRATCH"),
        })

    return DataTable(
        data=data,
        columns=[{"name": col, "id": col} for col in [
            "Date", "Time", "Symbol", "Side", "Qty",
            "Open", "Close", "P&L", "Result",
        ]],
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": _CHART_BG, "color": _ACCENT,
            "fontWeight": "bold", "border": f"1px solid {_GRID_COLOR}",
        },
        style_cell={
            "backgroundColor": _CHART_PAPER, "color": "#ccc",
            "border": f"1px solid {_GRID_COLOR}", "padding": "8px",
            "textAlign": "center",
        },
        style_data_conditional=[
            {
                "if": {"filter_query": '{Result} = "WIN"', "column_id": "P&L"},
                "color": _WIN_COLOR,
            },
            {
                "if": {"filter_query": '{Result} = "LOSS"', "column_id": "P&L"},
                "color": _LOSS_COLOR,
            },
            {
                "if": {"filter_query": '{Result} = "WIN"', "column_id": "Result"},
                "color": _WIN_COLOR,
            },
            {
                "if": {"filter_query": '{Result} = "LOSS"', "column_id": "Result"},
                "color": _LOSS_COLOR,
            },
        ],
        sort_action="native",
        page_size=20,
    )
