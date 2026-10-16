"""Deterministic governance layer for the ShopNest refund agent (policy v1.0).

No LLM here. Given structured facts (from the intake agent and the order database),
this returns the action the policy requires. In the full system the LLM decision agent
only *proposes*; this module has the final say.
"""

WINDOWS = {"electronics": 10, "fashion": 15}
DEFAULT_WINDOW = 7                      # R-01
NON_RETURNABLE = {"innerwear", "grocery", "personalized", "digital", "gift_card"}  # R-04
AUTO_APPROVE_LIMIT = 3000               # R-12 / R-13
MAX_REFUNDS_90D = 3                     # R-14
MAX_VALUE_30D = 10000                   # R-14
GRACE_DAYS = 3                          # R-17


def _result(action, amount=None, clauses=None):
    return {"action": action, "refund_amount": amount, "clauses": clauses or []}


def decide(order, customer, evidence, reason):
    """reason is one of: damaged, wrong_item, change_of_mind, not_received."""
    # 1. Order missing or not the customer's
    if order is None or not order["belongs_to_customer"]:
        return _result("deny", None, ["R-16"])

    full_refund = order["item_price"] + order["shipping_fee"]
    tracking = order["tracking_status"]

    # 2. Delivery problems (R-08)
    if reason == "not_received" or tracking in ("lost", "returned_to_origin", "in_transit"):
        if tracking == "in_transit":
            return _result("deny", None, ["R-08"])
        if tracking == "delivered":
            return _result("escalate", None, ["R-08"])
        amount, clauses = full_refund, ["R-08"]      # lost / returned to origin
    else:
        category = order["category"]
        damage_like = reason in ("damaged", "wrong_item")

        # 3. Non-returnable without damage / wrong item
        if category in NON_RETURNABLE and not damage_like:
            return _result("deny", None, ["R-04", "R-16"])

        # 4. Window check (R-05, R-16, R-17)
        window = WINDOWS.get(category, DEFAULT_WINDOW)
        late = order["days_since_delivery"] - window
        if late > GRACE_DAYS:
            return _result("deny", None, ["R-16"])
        if late >= 1:
            return _result("escalate", None, ["R-17"])

        # 5. Reason not allowed for category (R-02)
        if reason == "change_of_mind" and category == "electronics":
            return _result("deny", None, ["R-02", "R-16"])

        # 6. Evidence (R-18)
        if damage_like and evidence["photos"] < 1:
            return _result("request_info", None, ["R-18"])

        amount = full_refund if damage_like else order["item_price"]   # R-06/R-07 vs R-09
        clauses = ["R-06" if reason == "damaged" else "R-07" if reason == "wrong_item" else "R-09"]

    amount = min(amount, full_refund)                                   # R-20

    # 7. Risk checks (R-14, R-15)
    if customer["flagged"]:
        return _result("escalate", None, clauses + ["R-15"])
    if (customer["refunds_approved_90d"] >= MAX_REFUNDS_90D
            or customer["refund_value_30d"] + amount > MAX_VALUE_30D):
        return _result("escalate", None, clauses + ["R-14"])

    # 8. Amount limit (R-12, R-13)
    if amount > AUTO_APPROVE_LIMIT:
        return _result("escalate", None, clauses + ["R-13"])

    # 9. Approve
    return _result("approve", amount, clauses + ["R-12"])
