"""Business logic: one refund request end to end. No web framework in here."""
import json
import threading
import uuid

from agents.governed import decide_governed
from api.adapters import GatewayError
from api.db import utcnow


class ApiError(Exception):
    status = 400


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


class BadRequest(ApiError):
    status = 400


CUSTOMER_TEXT = {
    "approve": "Your refund of Rs {amount:,.0f} has been issued.",
    "deny": "Sorry, this request is not eligible for a refund under our return policy.",
    "escalate": "Thanks. A member of our team will review your request.",
    "request_info": "Please send a photo showing the problem so we can process your request.",
}


class RefundService:
    def __init__(self, db, store, gateway, intake, clock=utcnow):
        self.db, self.store, self.gateway, self.intake, self.clock = db, store, gateway, intake, clock

    # ---- kill switch
    def auto_approve_enabled(self):
        return self.db.get_setting("auto_approve", "1") == "1"

    def set_auto_approve(self, enabled):
        self.db.set_setting("auto_approve", "1" if enabled else "0")
        self.db.audit_append({"type": "kill_switch", "auto_approve": enabled}, self.clock().isoformat(timespec="seconds"))

    # ---- new request
    def submit(self, order_id, customer_id, message, photos):
        with self.db.lock:
            now = self.clock()
            ts = now.isoformat(timespec="seconds")
            request_id = uuid.uuid4().hex[:10]

            # 1. facts come from the store and our own records, never from the customer
            order = self.store.get_order(order_id)
            facts = None
            if order is not None:
                facts = {**order, "belongs_to_customer": order["customer_id"] == customer_id}
                facts.pop("customer_id")
            c90, v30 = self.db.customer_history(customer_id, now)
            customer = {"refunds_approved_90d": c90, "refund_value_30d": v30,
                        "flagged": self.store.is_flagged(customer_id)}
            evidence = {"photos": photos}

            # 2. intake (the only AI step); any failure fails closed
            intake_error = None
            try:
                reason = self.intake(message)
            except Exception as e:
                reason, intake_error = None, str(e)[:200]

            # 3. deterministic decision
            decision = decide_governed(facts, customer, evidence, reason)
            action, amount, clauses = decision["action"], decision["refund_amount"], list(decision["clauses"])

            # 4. controls outside the written policy
            if action == "approve" and self.db.refunded_total(order_id) > 0:
                action, amount, clauses = "escalate", None, clauses + ["DUPLICATE-GUARD"]
            if action == "approve" and not self.auto_approve_enabled():
                action, amount, clauses = "escalate", None, clauses + ["KILL-SWITCH"]

            # 5. act on the decision
            refund_id, status = None, {"deny": "denied", "escalate": "pending_review",
                                       "request_info": "awaiting_info"}.get(action)
            if action == "approve":
                try:
                    res = self.gateway.issue_refund(order_id, amount, idempotency_key=request_id)
                    refund_id, status = res["refund_id"], "refunded"
                    self.db.insert_refund(refund_id, request_id, order_id, customer_id, amount, ts, request_id, "auto")
                except GatewayError as e:
                    action, amount, status = "escalate", None, "pending_review"
                    clauses.append("GATEWAY-ERROR")
                    intake_error = intake_error or f"gateway: {e}"

            detail = {"facts": facts, "customer": customer, "evidence": evidence, "intake_error": intake_error}
            self.db.conn.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, ts, order_id, customer_id, message, photos, status, action, amount, reason,
                 json.dumps(clauses), json.dumps(detail)))
            self.db.conn.commit()
            self.db.audit_append({"type": "decision", "request_id": request_id, "order_id": order_id,
                                  "customer_id": customer_id, "message": message, "reason": reason,
                                  "action": action, "refund_amount": amount, "clauses": clauses,
                                  "refund_id": refund_id, "detail": detail}, ts)
            return {"request_id": request_id, "status": status, "action": action, "refund_amount": amount,
                    "reason": reason, "clauses": clauses, "refund_id": refund_id,
                    "customer_message": CUSTOMER_TEXT[action].format(amount=amount or 0)}

    def get_request(self, request_id):
        r = self.db.conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if r is None:
            raise NotFound("request not found")
        return {"request_id": r["request_id"], "created_at": r["created_at"], "order_id": r["order_id"],
                "customer_id": r["customer_id"], "message": r["message"], "status": r["status"],
                "action": r["action"], "refund_amount": r["refund_amount"], "reason": r["reason"],
                "clauses": json.loads(r["clauses"]), "detail": json.loads(r["detail"])}

    def pending(self):
        rows = self.db.conn.execute(
            "SELECT request_id FROM requests WHERE status='pending_review' ORDER BY created_at").fetchall()
        return [self.get_request(r["request_id"]) for r in rows]

    # ---- human review
    def review(self, request_id, reviewer, decision, amount, note):
        with self.db.lock:
            req = self.get_request(request_id)
            if req["status"] != "pending_review":
                raise Conflict(f"request is {req['status']}, not pending_review")
            ts = self.clock().isoformat(timespec="seconds")
            refund_id = None
            if decision == "approve":
                order = self.store.get_order(req["order_id"])
                if order is None:
                    raise BadRequest("order no longer exists")
                room = order["item_price"] + order["shipping_fee"] - self.db.refunded_total(req["order_id"])
                if amount <= 0 or amount > room + 1e-9:
                    raise BadRequest(f"amount must be between 0 and {room} (what is left of the payment)")
                try:
                    res = self.gateway.issue_refund(req["order_id"], amount, idempotency_key=f"{request_id}-human")
                except GatewayError as e:
                    raise BadRequest(str(e))
                refund_id = res["refund_id"]
                self.db.insert_refund(refund_id, request_id, req["order_id"], req["customer_id"], amount, ts,
                                      f"{request_id}-human", "human")
                status = "refunded_by_human"
            else:
                amount, status = 0.0, "rejected_by_human"
            self.db.conn.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?)",
                                 (request_id, reviewer, decision, amount, note, ts))
            self.db.conn.execute("UPDATE requests SET status=? WHERE request_id=?", (status, request_id))
            self.db.conn.commit()
            self.db.audit_append({"type": "human_review", "request_id": request_id, "reviewer": reviewer,
                                  "decision": decision, "refund_amount": amount, "note": note,
                                  "refund_id": refund_id}, ts)
            return {"request_id": request_id, "status": status, "refund_amount": amount, "refund_id": refund_id}
