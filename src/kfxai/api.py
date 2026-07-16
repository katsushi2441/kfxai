from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .config import load_settings
from .database import Database
from .engine import TradingEngine, market_is_open
from .models import utc_now_iso
from .oanda import OandaClient
from .research import dossier, run_research


settings = load_settings()
db = Database(settings.database_path)
app = FastAPI(title="Kurage FX AI Trade", version="0.1.0")
STATIC_DIR = Path(__file__).with_name("static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health(probe_oanda: bool = Query(default=False)) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "service": "kfxai",
        "version": app.version,
        "time": utc_now_iso(),
        "trading_mode": settings.trading_mode,
        "oanda_environment": settings.oanda_environment,
        "oanda_configured": bool(settings.account_id and settings.access_token),
        "market_open": market_is_open(),
        "judgment_backend": settings.judgment_backend,
    }
    if probe_oanda:
        if not result["oanda_configured"]:
            raise HTTPException(503, "OANDA credentials are not configured")
        try:
            result["oanda"] = OandaClient(settings).health()
        except Exception as exc:
            raise HTTPException(502, f"OANDA probe failed: {exc}") from exc
    return result


@app.get("/api/status")
def status() -> dict[str, Any]:
    trades = db.query("SELECT * FROM paper_trades ORDER BY id DESC LIMIT 100")
    decisions = db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT 100")
    cycles = db.query("SELECT * FROM cycles ORDER BY id DESC LIMIT 20")
    performance = dossier(db)
    return {
        "service": "Kurage FX AI Trade",
        "time": utc_now_iso(),
        "mode": settings.trading_mode,
        "environment": settings.oanda_environment,
        "market_open": market_is_open(),
        "instruments": settings.instruments,
        "backend": settings.judgment_backend,
        "regime": db.get_state("regime", {}),
        "directive": db.get_state("directive", {}),
        "last_cycle": db.get_state("last_cycle", {}),
        "last_error": db.get_state("last_error", {}),
        "performance": performance,
        "open_trades": [trade for trade in trades if trade["status"] == "open"],
        "recent_trades": trades,
        "recent_decisions": decisions,
        "recent_cycles": cycles,
        "research": db.query("SELECT * FROM research ORDER BY id DESC LIMIT 20"),
    }


@app.post("/api/control/cycle")
def control_cycle() -> dict[str, Any]:
    if not settings.enable_control_api:
        raise HTTPException(403, "control API is disabled")
    settings.validate(require_credentials=True)
    return TradingEngine(settings).run_cycle()


@app.post("/api/control/research")
def control_research() -> dict[str, Any]:
    if not settings.enable_control_api:
        raise HTTPException(403, "control API is disabled")
    return run_research()

