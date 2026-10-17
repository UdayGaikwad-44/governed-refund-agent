"""Adapter layer: the only two places where the system touches the outside world.

To connect a real store, implement these two interfaces:
  OrderStore    - read order facts (from the store's order API) and account flags
  RefundGateway - issue a refund (via the payment provider's refund API)
Everything else (intake, rule engine, audit log, review queue) stays unchanged.
The mocks below are for development and tests only.
"""
import json
import threading
import uuid
from pathlib import Path
from typing import Optional, Protocol

ROOT = Path(__file__).resolve().parents[2]
MOCK_DIR = ROOT / "data" / "mock"


class GatewayError(Exception):
    """The payment side refused or failed the refund."""


class OrderStore(Protocol):
    def get_order(self, order_id: str) -> Optional[dict]:
        """Return {order_id, customer_id, category, item_price, shipping_fee,
        days_since_delivery (or None), tracking_status}, or None if the order doesn't exist."""

    def is_flagged(self, customer_id: str) -> bool: ...


class RefundGateway(Protocol):
    def issue_refund(self, order_id: str, amount: float, idempotency_key: str) -> dict:
        """Return {refund_id, amount, replayed}. Must be idempotent per key and must itself
        refuse to refund more than was paid (defence in depth, independent of our rules)."""


class MockOrderStore:
    def __init__(self, orders_path=MOCK_DIR / "orders.json", customers_path=MOCK_DIR / "customers.json"):
        self._orders = {o["order_id"]: o for o in json.loads(Path(orders_path).read_text())}
        self._flags = {c["customer_id"]: c["flagged"] for c in json.loads(Path(customers_path).read_text())}

    def get_order(self, order_id):
        o = self._orders.get(order_id)
        if o is None:
            return None
        return {"order_id": o["order_id"], "customer_id": o["customer_id"], "category": o["category"],
                "item_price": o["item_price"], "shipping_fee": o["shipping_fee"],
                "days_since_delivery": o["delivered_days_ago"], "tracking_status": o["tracking_status"]}

    def is_flagged(self, customer_id):
        return self._flags.get(customer_id, False)


class MockRefundGateway:
    """Pretends to be a payment provider: idempotent, and refuses to refund more than captured."""

    def __init__(self, store: OrderStore):
        self._store = store
        self._by_key = {}
        self._refunded = {}
        self._lock = threading.Lock()

    def issue_refund(self, order_id, amount, idempotency_key):
        with self._lock:
            if idempotency_key in self._by_key:
                return {**self._by_key[idempotency_key], "replayed": True}
            order = self._store.get_order(order_id)
            if order is None:
                raise GatewayError("unknown order")
            captured = order["item_price"] + order["shipping_fee"]
            already = self._refunded.get(order_id, 0.0)
            if amount <= 0 or already + amount > captured + 1e-9:
                raise GatewayError(f"refund {amount} exceeds captured payment ({captured}, already refunded {already})")
            result = {"refund_id": "RF-" + uuid.uuid4().hex[:10], "amount": amount}
            self._by_key[idempotency_key] = result
            self._refunded[order_id] = already + amount
            return {**result, "replayed": False}
