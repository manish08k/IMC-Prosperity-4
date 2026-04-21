from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple
import json
import statistics


class Trader:
    POSITION_LIMITS = {
        "INTARIAN_PEPPER_ROOT": 50,
        "ASH_COATED_OSMIUM": 50,
    }

    def __init__(self):
        self.price_history: Dict[str, List[float]] = {}

    def mid(self, od: OrderDepth) -> float | None:
        if od.buy_orders and od.sell_orders:
            return (max(od.buy_orders) + min(od.sell_orders)) / 2
        return float(max(od.buy_orders)) if od.buy_orders else (
               float(min(od.sell_orders)) if od.sell_orders else None)

    def update_history(self, product: str, price: float):
        h = self.price_history.setdefault(product, [])
        h.append(price)
        if len(h) > 300:
            h.pop(0)

    def ema(self, prices: List[float], span: int) -> float:
        alpha = 2.0 / (span + 1)
        e = prices[0]
        for p in prices[1:]:
            e = alpha * p + (1 - alpha) * e
        return e

    def zscore(self, prices: List[float], window: int) -> float:
        if len(prices) < window:
            return 0.0
        recent = prices[-window:]
        mu = statistics.mean(recent)
        sigma = statistics.pstdev(recent)
        return (prices[-1] - mu) / sigma if sigma > 0 else 0.0

    def take_liquidity(self, product: str, od: OrderDepth,
                       fair: float, pos: int, limit: int,
                       buy_edge: float, sell_edge: float) -> Tuple[List[Order], int]:
        orders = []
        if od.sell_orders:
            for price in sorted(od.sell_orders.keys()):
                if price <= fair - buy_edge:
                    qty = min(abs(od.sell_orders[price]), limit - pos)
                    if qty > 0:
                        orders.append(Order(product, price, qty))
                        pos += qty
                else:
                    break
        if od.buy_orders:
            for price in sorted(od.buy_orders.keys(), reverse=True):
                if price >= fair + sell_edge:
                    qty = min(abs(od.buy_orders[price]), limit + pos)
                    if qty > 0:
                        orders.append(Order(product, price, -qty))
                        pos -= qty
                else:
                    break
        return orders, pos

    def make_market(self, product: str, fair: float, pos: int, limit: int,
                    bid_off: int, ask_off: int, size: int) -> List[Order]:
        orders = []
        bid_qty = min(size, limit - pos)
        ask_qty = min(size, limit + pos)
        if bid_qty > 0:
            orders.append(Order(product, int(fair) - bid_off, bid_qty))
        if ask_qty > 0:
            orders.append(Order(product, int(fair) + ask_off, -ask_qty))
        return orders

    # ── ASH_COATED_OSMIUM ─────────────────────────────────────────────────────
    # Mid price oscillates ~9990–10019. EMA fair ~10003–10009.
    # Spread ~16–18 wide. Pure mean-reversion + tight MM.

    def trade_osmium(self, state: TradingState) -> List[Order]:
        product = "ASH_COATED_OSMIUM"
        if product not in state.order_depths:
            return []
        od = state.order_depths[product]
        pos = state.position.get(product, 0)
        limit = self.POSITION_LIMITS[product]

        mid = self.mid(od)
        if mid is None:
            return []
        self.update_history(product, mid)
        prices = self.price_history[product]

        fair = self.ema(prices, 15) if len(prices) >= 5 else mid
        z = self.zscore(prices, 40)

        orders = []

        # 1. Aggressive take on strong reversion signal
        if z < -1.8 and pos < limit:
            if od.sell_orders:
                best = min(od.sell_orders.keys())
                qty = min(abs(od.sell_orders[best]), limit - pos, 20)
                if qty > 0:
                    orders.append(Order(product, best, qty))
                    pos += qty
        elif z > 1.8 and pos > -limit:
            if od.buy_orders:
                best = max(od.buy_orders.keys())
                qty = min(abs(od.buy_orders[best]), limit + pos, 20)
                if qty > 0:
                    orders.append(Order(product, best, -qty))
                    pos -= qty

        # 2. Take clear mispricing vs fair (edge > 4)
        take, pos = self.take_liquidity(product, od, fair, pos, limit,
                                        buy_edge=4, sell_edge=4)
        orders += take

        # 3. Passive MM — inventory-skewed quotes
        # If long, lower bid / keep ask; if short, keep bid / lower ask
        inv_skew = max(-2, min(2, pos // 10))
        bid_off = 2 - inv_skew   # widen bid when long (don't accumulate more)
        ask_off = 2 + inv_skew   # widen ask when short

        orders += self.make_market(product, fair, pos, limit,
                                   bid_off=max(1, bid_off),
                                   ask_off=max(1, ask_off),
                                   size=15)
        return orders

    # ── INTARIAN_PEPPER_ROOT ──────────────────────────────────────────────────
    # Trends slowly upward ~13000 → ~13110 over day 1.
    # Spread ~14 wide. Strategy: trend + tight MM around slow EMA.

    def trade_pepper(self, state: TradingState) -> List[Order]:
        product = "INTARIAN_PEPPER_ROOT"
        if product not in state.order_depths:
            return []
        od = state.order_depths[product]
        pos = state.position.get(product, 0)
        limit = self.POSITION_LIMITS[product]

        mid = self.mid(od)
        if mid is None:
            return []
        self.update_history(product, mid)
        prices = self.price_history[product]

        fair = self.ema(prices, 20) if len(prices) >= 5 else mid
        z = self.zscore(prices, 40)

        orders = []

        if len(prices) >= 10:
            fast = self.ema(prices, 5)
            slow = self.ema(prices, 30)
            trend = fast - slow  # positive = price rising

            # Ride the trend: if uptrend and short or flat, buy
            if trend > 4 and pos < limit - 10:
                if od.sell_orders:
                    best = min(od.sell_orders.keys())
                    if best <= fair + 8:
                        qty = min(abs(od.sell_orders[best]), limit - pos, 12)
                        if qty > 0:
                            orders.append(Order(product, best, qty))
                            pos += qty

            # If downtrend, sell down
            elif trend < -4 and pos > -(limit - 10):
                if od.buy_orders:
                    best = max(od.buy_orders.keys())
                    if best >= fair - 8:
                        qty = min(abs(od.buy_orders[best]), limit + pos, 12)
                        if qty > 0:
                            orders.append(Order(product, best, -qty))
                            pos -= qty

        # Mean reversion on big z-score moves
        if z < -2.5 and pos < limit:
            if od.sell_orders:
                best = min(od.sell_orders.keys())
                qty = min(abs(od.sell_orders[best]), limit - pos, 15)
                if qty > 0:
                    orders.append(Order(product, best, qty))
                    pos += qty
        elif z > 2.5 and pos > -limit:
            if od.buy_orders:
                best = max(od.buy_orders.keys())
                qty = min(abs(od.buy_orders[best]), limit + pos, 15)
                if qty > 0:
                    orders.append(Order(product, best, -qty))
                    pos -= qty

        # Take mispriced liquidity
        take, pos = self.take_liquidity(product, od, fair, pos, limit,
                                        buy_edge=3, sell_edge=3)
        orders += take

        # Passive MM around slow EMA
        inv_skew = max(-2, min(2, pos // 10))
        orders += self.make_market(product, fair, pos, limit,
                                   bid_off=max(1, 2 - inv_skew),
                                   ask_off=max(1, 2 + inv_skew),
                                   size=12)
        return orders

    # ── main ─────────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        osmium = self.trade_osmium(state)
        if osmium:
            result["ASH_COATED_OSMIUM"] = osmium

        pepper = self.trade_pepper(state)
        if pepper:
            result["INTARIAN_PEPPER_ROOT"] = pepper

        trader_data = json.dumps({
            "ph": {k: v[-60:] for k, v in self.price_history.items()},
        })

        return result, conversions, trader_data