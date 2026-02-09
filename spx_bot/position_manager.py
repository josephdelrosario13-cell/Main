"""
Position manager - tracks open positions and handles exit logic.

Monitors all open spread positions and triggers exits based on:
- Profit target (default: 50% of max profit)
- Stop loss (default: 2x credit received)
- Time-based exit (force close before market close)
- Risk manager override (daily drawdown breached)
"""

import logging
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from spx_bot.config import AppConfig, config
from spx_bot.market_data import MarketDataProvider, OptionType
from spx_bot.orders import (
    Fill,
    Order,
    OrderExecutor,
    OrderType,
    Position,
    SpreadLeg,
    SpreadSide,
)
from spx_bot.risk_manager import RiskManager

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class PositionManager:
    """Manages open positions and exit logic."""

    def __init__(
        self,
        executor: OrderExecutor,
        market_data: MarketDataProvider,
        risk_manager: RiskManager,
        cfg: AppConfig = config,
    ):
        self.executor = executor
        self.market_data = market_data
        self.risk_manager = risk_manager
        self.cfg = cfg
        self.positions: list[Position] = []
        self._position_counter = 0

    def open_position(self, order: Order, fill: Fill, underlying_price: float) -> Position:
        """Record a new open position from a filled order."""
        self._position_counter += 1
        pos = Position(
            position_id=f"POS-{self._position_counter:04d}",
            spread_side=order.spread_side,
            short_strike=order.short_strike,
            long_strike=order.long_strike,
            quantity=fill.quantity,
            entry_credit=fill.fill_price,
            spread_width=order.spread_width,
            entry_time=fill.timestamp,
            underlying_at_entry=underlying_price,
        )
        self.positions.append(pos)
        self.risk_manager.record_trade_open()

        logger.info(
            "OPENED %s | %s %s/%s x%d @ $%.2f | Max profit: $%.2f | Max loss: $%.2f",
            pos.position_id,
            pos.spread_side.value,
            pos.short_strike,
            pos.long_strike,
            pos.quantity,
            pos.entry_credit,
            pos.max_profit,
            pos.max_loss,
        )
        return pos

    def monitor_positions(self) -> list[dict]:
        """
        Check all open positions against exit criteria.

        Returns a list of actions taken (for logging/alerting).
        """
        actions = []
        chain = self.market_data.get_options_chain()
        now = datetime.now(ET)
        force_close = time(*map(int, self.cfg.strategy.force_close_time.split(":")))

        total_unrealized = 0.0

        for pos in self.positions:
            if not pos.is_open:
                continue

            # Get current mid price to close the spread
            current_mid = self._get_spread_mid(chain, pos)
            if current_mid is None:
                logger.warning("Cannot get mid for %s, skipping", pos.position_id)
                continue

            unrealized = pos.unrealized_pnl(current_mid)
            total_unrealized += unrealized

            # Exit check 1: Profit target
            if pos.should_take_profit(current_mid, self.cfg.strategy.profit_target_pct):
                action = self._close_position(pos, current_mid, "PROFIT_TARGET")
                actions.append(action)
                continue

            # Exit check 2: Stop loss
            if pos.should_stop_loss(current_mid, self.cfg.strategy.stop_loss_multiple):
                action = self._close_position(pos, current_mid, "STOP_LOSS")
                actions.append(action)
                continue

            # Exit check 3: Force close before market close
            if now.time() >= force_close:
                action = self._close_position(pos, current_mid, "TIME_EXIT")
                actions.append(action)
                continue

            # Exit check 4: Risk manager says close everything
            if self.risk_manager.state.max_drawdown_hit:
                action = self._close_position(pos, current_mid, "RISK_OVERRIDE")
                actions.append(action)
                continue

        # Update risk manager with total unrealized P&L
        self.risk_manager.update_unrealized(total_unrealized)

        return actions

    def close_all_positions(self, reason: str = "MANUAL") -> list[dict]:
        """Emergency close of all open positions."""
        actions = []
        chain = self.market_data.get_options_chain()

        for pos in self.positions:
            if not pos.is_open:
                continue
            current_mid = self._get_spread_mid(chain, pos)
            if current_mid is None:
                current_mid = pos.entry_credit  # Assume scratch if no data
            action = self._close_position(pos, current_mid, reason)
            actions.append(action)

        return actions

    def _close_position(self, pos: Position, close_price: float, reason: str) -> dict:
        """Execute a position close."""
        # Build closing order (buy back the spread)
        if pos.spread_side == SpreadSide.BULL_PUT:
            legs = [
                SpreadLeg(
                    symbol=f"SPX_P{pos.short_strike:.0f}",
                    strike=pos.short_strike,
                    action="BUY",
                    quantity=pos.quantity,
                    option_type="put",
                ),
                SpreadLeg(
                    symbol=f"SPX_P{pos.long_strike:.0f}",
                    strike=pos.long_strike,
                    action="SELL",
                    quantity=pos.quantity,
                    option_type="put",
                ),
            ]
        else:
            legs = [
                SpreadLeg(
                    symbol=f"SPX_C{pos.short_strike:.0f}",
                    strike=pos.short_strike,
                    action="BUY",
                    quantity=pos.quantity,
                    option_type="call",
                ),
                SpreadLeg(
                    symbol=f"SPX_C{pos.long_strike:.0f}",
                    strike=pos.long_strike,
                    action="SELL",
                    quantity=pos.quantity,
                    option_type="call",
                ),
            ]

        close_order = Order(
            spread_side=pos.spread_side,
            legs=legs,
            quantity=pos.quantity,
            order_type=OrderType.DEBIT,
            limit_price=close_price,
        )

        fill = self.executor.submit_order(close_order)

        pos.exit_credit = fill.fill_price
        pos.exit_time = fill.timestamp
        pos.is_open = False

        pnl = pos.realized_pnl or 0.0
        self.risk_manager.record_trade_close(pnl)

        action = {
            "position_id": pos.position_id,
            "reason": reason,
            "entry_credit": pos.entry_credit,
            "exit_debit": fill.fill_price,
            "pnl": pnl,
            "quantity": pos.quantity,
        }

        logger.info(
            "CLOSED %s | Reason: %s | Entry: $%.2f | Exit: $%.2f | P&L: $%.2f",
            pos.position_id,
            reason,
            pos.entry_credit,
            fill.fill_price,
            pnl,
        )
        return action

    def _get_spread_mid(self, chain, pos: Position) -> Optional[float]:
        """Get the current mid price to close a spread."""
        if pos.spread_side == SpreadSide.BULL_PUT:
            short_opt = chain.find_by_strike(OptionType.PUT, pos.short_strike)
            long_opt = chain.find_by_strike(OptionType.PUT, pos.long_strike)
        else:
            short_opt = chain.find_by_strike(OptionType.CALL, pos.short_strike)
            long_opt = chain.find_by_strike(OptionType.CALL, pos.long_strike)

        if short_opt is None or long_opt is None:
            return None

        # Cost to buy back = short_mid - long_mid
        return round(short_opt.mid - long_opt.mid, 2)

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.is_open]

    @property
    def closed_positions(self) -> list[Position]:
        return [p for p in self.positions if not p.is_open]

    def get_summary(self) -> dict:
        """Return a summary of all positions."""
        open_pos = self.open_positions
        closed_pos = self.closed_positions
        total_realized = sum(p.realized_pnl or 0 for p in closed_pos)

        return {
            "open_count": len(open_pos),
            "closed_count": len(closed_pos),
            "total_realized_pnl": round(total_realized, 2),
            "positions": [
                {
                    "id": p.position_id,
                    "side": p.spread_side.value,
                    "strikes": f"{p.short_strike}/{p.long_strike}",
                    "qty": p.quantity,
                    "entry": p.entry_credit,
                    "exit": p.exit_credit,
                    "pnl": p.realized_pnl,
                    "open": p.is_open,
                }
                for p in self.positions
            ],
        }
