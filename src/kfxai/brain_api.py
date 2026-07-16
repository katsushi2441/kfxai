from __future__ import annotations

import os
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import load_settings
from .judgment import LocalLlmBackend, RuleBackend
from .models import utc_now_iso


class MarketPayload(BaseModel):
    ohlcv_by_instrument: dict[str, list[list[Any]]] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class TradePayload(BaseModel):
    ctx: dict[str, Any]


class ResearchPayload(BaseModel):
    dossier: dict[str, Any]


settings = load_settings()
engine_name = os.getenv("KFXAI_BRAIN_ENGINE", "rule_based").strip().lower()
if engine_name == "local_llm":
    backend = LocalLlmBackend(settings)
elif engine_name == "rule_based":
    backend = RuleBackend()
else:
    raise RuntimeError("KFXAI_BRAIN_ENGINE must be rule_based or local_llm")

app = FastAPI(
    title="Kurage FX AI Trade Brain",
    version="0.1.0",
    description="Stateless judgment endpoints intended to sit behind an x402 gateway.",
)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "kfxai-brain", "engine": backend.name, "time": utc_now_iso()}


@app.get("/v1/meta")
def meta() -> dict[str, Any]:
    return {
        "service": "Kurage FX AI Trade Brain",
        "version": app.version,
        "engine": backend.name,
        "capabilities": ["regime", "directive", "postmortem", "research_hypotheses"],
        "can_place_orders": False,
    }


@app.post("/v1/judge/regime")
def regime(payload: MarketPayload) -> dict[str, Any]:
    if not payload.ohlcv_by_instrument:
        raise HTTPException(422, "ohlcv_by_instrument is required")
    return backend.classify_regime(payload.ohlcv_by_instrument)


@app.post("/v1/judge/directive")
def directive(payload: MarketPayload) -> dict[str, Any]:
    if not payload.ohlcv_by_instrument:
        raise HTTPException(422, "ohlcv_by_instrument is required")
    return backend.risk_directive(payload.ohlcv_by_instrument, payload.history)


@app.post("/v1/judge/postmortem")
def postmortem(payload: TradePayload) -> dict[str, Any]:
    return backend.review_trade(payload.ctx)


@app.post("/v1/research/hypotheses")
def hypotheses(payload: ResearchPayload) -> dict[str, Any]:
    return {"hypotheses": backend.propose_hypotheses(payload.dossier), "model": backend.name}


def main() -> int:
    uvicorn.run(
        "kfxai.brain_api:app",
        host=os.getenv("KFXAI_BRAIN_HOST", "127.0.0.1"),
        port=int(os.getenv("KFXAI_BRAIN_PORT", "18325")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
