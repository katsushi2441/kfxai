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


def _agent_performance() -> list[dict[str, Any]]:
    """投資家レーン別の評価: 予算・残高・収益率・状態。成績は投資家単位(戦略帰属なし)。"""
    rows = db.query(
        "SELECT strategy, "
        "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS trades, "
        "SUM(CASE WHEN status='closed' AND pnl_jpy > 0 THEN 1 ELSE 0 END) AS wins, "
        "ROUND(SUM(CASE WHEN status='closed' THEN pnl_jpy ELSE 0 END), 0) AS pnl_jpy, "
        "SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_now, "
        "ROUND(SUM(CASE WHEN status='closed' AND date(close_time)=date('now') "
        "THEN pnl_jpy ELSE 0 END), 0) AS today_pnl "
        "FROM paper_trades GROUP BY strategy ORDER BY pnl_jpy DESC"
    )
    # arenaで構成中のエージェントは未取引でもゼロ行で出す(ダッシュボードの
    # 枠表示がエージェント全員分になるように)。構成外=過去戦略はarena=False。
    # 稼働中の投資家レーンは未取引でもゼロ行で出す。過去の単体戦略(session等)の
    # トレードはレーン外=arena=Falseとして履歴表示(旧アーキテクチャの遺物)。
    # レーン構成: production=本番(昇格先)、arena=投資家A/B/C。どちらも未取引でもゼロ行で出す。
    # 過去の単体戦略トレード(session等)はレーン外=履歴(legacy)。
    configured: dict[str, dict] = {}
    production_names: set[str] = set()
    if settings.strategy == "arena":
        from .strategies import build_production, build_strategies

        for inv in build_production(settings):
            production_names.add(inv.name)
            configured[inv.name] = {"subs": [s.name for s in getattr(inv, "subs", [])]}
        for inv in build_strategies(settings):
            configured[inv.name] = {"subs": [s.name for s in getattr(inv, "subs", [])]}
        existing = {row["strategy"] for row in rows}
        for name in configured:
            if name not in existing:
                rows.append({"strategy": name, "trades": 0, "wins": 0,
                             "pnl_jpy": 0, "open_now": 0, "today_pnl": 0})
    budget = settings.agent_budget_jpy
    dd_limit = budget * settings.agent_max_drawdown_pct / 100
    for row in rows:
        pnl = float(row["pnl_jpy"] or 0)
        name = row["strategy"]
        row["budget_jpy"] = budget
        row["equity_jpy"] = round(budget + pnl)
        row["return_pct"] = round(100 * pnl / budget, 3) if budget else 0
        row["status"] = "suspended" if pnl <= -dd_limit else "active"
        row["production"] = name in production_names
        row["arena"] = name in configured and name not in production_names
        row["max_positions"] = settings.max_positions if name in configured else None
        row["subs"] = configured.get(name, {}).get("subs", [])
    # 本番→アリーナ(損益順)→履歴 の順。
    rows.sort(key=lambda r: (not r["production"], not r["arena"], -float(r["pnl_jpy"] or 0)))
    return rows


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
        "max_positions": settings.max_positions,
        "strategy_mode": settings.strategy,
        "agent_budget_jpy": settings.agent_budget_jpy,
        "strategy_performance": _agent_performance(),
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

