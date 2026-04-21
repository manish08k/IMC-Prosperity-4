from datamodel import Order, TradingState
from typing import List, Dict, Tuple
import jsonpickle

# ── POSITION LIMITS ──────────────────────────────────────────────────────────
LIMITS = {
    "INTARIAN_PEPPER_ROOT": 80,
    "ASH_COATED_OSMIUM":    50,
}

# ASH fair value: confirmed across all 3 days of historical data.
# Filtered means: Day-2=9998, Day-1=10001, Day0=10002.
# Fixed anchor = 10000. EMA-based approaches drift and lose edge.
ASH_FAIR = 10000

# ASH spread constant: observed spread = 16 ticks across all 3 days.
ASH_SPREAD = 16


class Trader:
    """
    Optimised two-instrument strategy for IMC Prosperity Round 1.

    PEPPER: Sweep all ask levels → hold max long → ride +1001 tick/day trend.
    ASH:    Tiered market maker around fixed fair=10000 with strong inventory skew.
    """

    def run(self, state: TradingState) -> Tuple[Dict, int, str]:
        result: Dict[str, List[Order]] = {}
        conversions = 0

        for product, order_depth in state.order_depths.items():
            pos   = state.position.get(product, 0)
            limit = LIMITS.get(product, 50)

            if product == "INTARIAN_PEPPER_ROOT":
                orders = self._pepper(product, order_depth, pos, limit)
            elif product == "ASH_COATED_OSMIUM":
                orders = self._ash(product, order_depth, pos, limit)
            else:
                orders = []

            result[product] = orders

        return result, conversions, ""
    def _pepper(self, product: str, od, pos: int, limit: int) -> List[Order]:
        orders: List[Order] = []
        need = limit - pos   # Units needed to reach full position

        if need <= 0:
            return orders   # Already maxed — nothing to do

        # ── Layer 1: Sweep ALL available ask levels ──────────────────────────
        # Sort asks cheapest-first to minimise entry cost.
        # Max 3 levels available historically; sweep all until need=0.
        for ask_px in sorted(od.sell_orders.keys()):
            if need <= 0:
                break
            ask_vol = abs(od.sell_orders[ask_px])
            qty = min(ask_vol, need)
            if qty > 0:
                orders.append(Order(product, ask_px, qty))
                need -= qty

        # ── Layer 2: Passive bid if still unfilled ───────────────────────────
        # Posts 2 ticks above best bid — competitive but not aggressive cross.
        # Fills when a seller arrives at the spread or price dips momentarily.
        if need > 0:
            if od.buy_orders:
                bid_px = max(od.buy_orders.keys()) + 2
            elif od.sell_orders:
                # No bids at all — sit just below best ask (cross if seller drops)
                bid_px = min(od.sell_orders.keys()) - 1
            else:
                bid_px = 12000  # Emergency fallback (rarely triggered)
            orders.append(Order(product, bid_px, need))

        return orders
    def _ash(self, product: str, od, pos: int, limit: int) -> List[Order]:
        orders: List[Order] = []
        fair = ASH_FAIR   # Fixed = 10000

        has_bid = bool(od.buy_orders)
        has_ask = bool(od.sell_orders)

        # ── Compute best market prices ───────────────────────────────────────
        if has_bid and has_ask:
            best_bid = max(od.buy_orders.keys())
            best_ask = min(od.sell_orders.keys())
        elif has_bid:
            best_bid = max(od.buy_orders.keys())
            best_ask = best_bid + ASH_SPREAD   # Assume normal spread
        elif has_ask:
            best_ask = min(od.sell_orders.keys())
            best_bid = best_ask - ASH_SPREAD
        else:
            # Completely empty book: quote symmetrically around fair
            best_bid = fair - 8
            best_ask = fair + 8

        # ── LAYER 1: AGGRESSIVE TAKER ────────────────────────────────────────
        # Guaranteed edge: any price strictly inside fair is free money.
        # We also sweep multiple levels if they're all below/above fair.
        # pos updated locally so capacity checks below are accurate.

        if has_ask:
            for ask_px in sorted(od.sell_orders.keys()):
                if ask_px >= fair:
                    break
                buy_cap = limit - pos
                if buy_cap <= 0:
                    break
                vol = min(abs(od.sell_orders[ask_px]), buy_cap)
                if vol > 0:
                    orders.append(Order(product, ask_px, vol))
                    pos += vol

        if has_bid:
            for bid_px in sorted(od.buy_orders.keys(), reverse=True):
                if bid_px <= fair:
                    break
                sell_cap = limit + pos
                if sell_cap <= 0:
                    break
                vol = min(abs(od.buy_orders[bid_px]), sell_cap)
                if vol > 0:
                    orders.append(Order(product, bid_px, -vol))
                    pos -= vol

        # ── LAYER 3: COMPUTE INVENTORY SKEW ─────────────────────────────────
        # Nonlinear skew — stronger near limits to prevent position lock-up.
        # skew > 0 → we're long → shift quotes DOWN to encourage selling
        # skew < 0 → we're short → shift quotes UP to encourage buying
        #
        # Formula: skew = sign(pos) × round(9 × sqrt(|pos| / limit))
        # Comparison with v4 linear skew (7 × pos/limit):
        #   pos=10: v4=1.4  v5=2.8  (more aggressive at moderate positions)
        #   pos=25: v4=3.5  v5=4.5  (moderately stronger)
        #   pos=40: v4=5.6  v5=5.7  (similar at 80%)
        #   pos=50: v4=7.0  v5=9.0  (much stronger at full limit)
        if pos == 0:
            skew = 0
        else:
            import math
            sign = 1 if pos > 0 else -1
            skew = sign * int(round(9.0 * math.sqrt(abs(pos) / limit)))

        # ── LAYER 4: EMERGENCY UNWIND (70% threshold) ────────────────────────
        emergency_threshold = int(limit * 0.70)   # = 35

        # ── LAYER 2: TIERED MM QUOTES ────────────────────────────────────────
        # Inner quote (70% of capacity): best_bid+1 / best_ask-1 with skew
        # Outer quote (30% of capacity): fair-3 / fair+3 with skew
        # Both quotes are skewed to encourage inventory mean-reversion.

        inner_bid = best_bid + 1 - skew
        inner_ask = best_ask - 1 - skew
        outer_bid = fair - 3 - skew
        outer_ask = fair + 3 - skew

        # Emergency override: force aggressive unwind price
        if pos > emergency_threshold:
            inner_ask = min(inner_ask, fair - 1)
            outer_ask = min(outer_ask, fair - 1)
        elif pos < -emergency_threshold:
            inner_bid = max(inner_bid, fair + 1)
            outer_bid = max(outer_bid, fair + 1)

        
        inner_bid = min(inner_bid, fair - 1)
        outer_bid = min(outer_bid, fair - 1)

        if pos <= emergency_threshold:
            inner_ask = max(inner_ask, fair + 1)
            outer_ask = max(outer_ask, fair + 1)

        # Prevent crossed quotes (would match ourselves — wasteful)
        if inner_bid >= inner_ask:
            inner_bid = fair - 1
            inner_ask = fair + 1
        if outer_bid >= outer_ask:
            outer_bid = fair - 2
            outer_ask = fair + 2

        # Hard bounds: never more than 12 ticks from fair
        inner_bid = max(inner_bid, fair - 12)
        inner_ask = min(inner_ask, fair + 12)
        outer_bid = max(outer_bid, fair - 12)
        outer_ask = min(outer_ask, fair + 12)

        # ── CAPACITY ALLOCATION ──────────────────────────────────────────────
        # Split remaining capacity 70% to inner quote, 30% to outer quote.
        # Outer quotes sit passively deeper and use leftover capacity.
        buy_cap  = limit - pos
        sell_cap = limit + pos

        inner_buy_size  = int(buy_cap  * 0.70)
        inner_sell_size = int(sell_cap * 0.70)
        outer_buy_size  = buy_cap  - inner_buy_size
        outer_sell_size = sell_cap - inner_sell_size

        # ── POST QUOTES ──────────────────────────────────────────────────────
        if inner_buy_size > 0 and inner_bid > 0:
            orders.append(Order(product, inner_bid, inner_buy_size))
        if outer_buy_size > 0 and outer_bid > 0 and outer_bid != inner_bid:
            orders.append(Order(product, outer_bid, outer_buy_size))

        if inner_sell_size > 0 and inner_ask > 0:
            orders.append(Order(product, inner_ask, -inner_sell_size))
        if outer_sell_size > 0 and outer_ask > 0 and outer_ask != inner_ask:
            orders.append(Order(product, outer_ask, -outer_sell_size))

        return orders