"""Governed Refund API.

Run (from the project root):
    uvicorn api.main:app --app-dir src --reload
Environment (.env): API_KEY, INTAKE_MODE=keyword|llm, and for llm: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
Docs once running: http://127.0.0.1:8000/docs
"""
import os
import secrets
import sys
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.security import APIKeyHeader  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from api.adapters import MockOrderStore, MockRefundGateway  # noqa: E402
from api.db import Database  # noqa: E402
from api.service import ApiError, RefundService  # noqa: E402


class RefundRequestIn(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    photos: int = Field(0, ge=0, le=20)


class ReviewIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=80)
    decision: Literal["approve", "reject"]
    amount: float = Field(0, ge=0)
    note: str = Field("", max_length=500)


class KillSwitchIn(BaseModel):
    auto_approve: bool


def make_intake():
    if os.environ.get("INTAKE_MODE", "keyword") == "llm":
        from agents.baseline import make_client
        from agents.governed import classify_reason
        client, model = make_client(), os.environ["LLM_MODEL"]
        return lambda message: classify_reason(client, model, message)
    from webapp.pipeline import keyword_reason
    return keyword_reason


def create_app(db=None, store=None, gateway=None, intake=None, api_key=None):
    api_key = api_key or os.environ.get("API_KEY", "dev-key")
    if db is None:
        path = ROOT / "data" / "runtime" / "api.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(str(path))
    store = store or MockOrderStore()
    gateway = gateway or MockRefundGateway(store)
    service = RefundService(db, store, gateway, intake or make_intake())

    app = FastAPI(title="Governed Refund API", version="0.1")
    app.state.service = service

    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)   # gives the docs page an Authorize button

    def auth(x_api_key: str | None = Depends(key_header)):
        if x_api_key is None or not secrets.compare_digest(x_api_key, api_key):
            raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")

    @app.exception_handler(ApiError)
    async def api_error_handler(_, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/refund-requests", dependencies=[Depends(auth)])
    def create_request(body: RefundRequestIn):
        return service.submit(body.order_id, body.customer_id, body.message, body.photos)

    @app.get("/refund-requests/{request_id}", dependencies=[Depends(auth)])
    def get_request(request_id: str):
        return service.get_request(request_id)

    @app.get("/reviews/pending", dependencies=[Depends(auth)])
    def pending():
        return service.pending()

    @app.post("/reviews/{request_id}", dependencies=[Depends(auth)])
    def review(request_id: str, body: ReviewIn):
        return service.review(request_id, body.reviewer, body.decision, body.amount, body.note)

    @app.get("/admin/kill-switch", dependencies=[Depends(auth)])
    def get_kill_switch():
        return {"auto_approve": service.auto_approve_enabled()}

    @app.post("/admin/kill-switch", dependencies=[Depends(auth)])
    def set_kill_switch(body: KillSwitchIn):
        service.set_auto_approve(body.auto_approve)
        return {"auto_approve": service.auto_approve_enabled()}

    @app.get("/audit", dependencies=[Depends(auth)])
    def audit(limit: int = 50):
        return db.audit_recent(min(max(limit, 1), 500))

    @app.get("/audit/verify", dependencies=[Depends(auth)])
    def audit_verify():
        ok, bad = db.audit_verify()
        return {"ok": ok, "first_bad_entry": bad}

    return app


app = create_app()
