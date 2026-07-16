from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path


LIVE_ACK = "I_UNDERSTAND_THIS_USES_REAL_MONEY"


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    account_id: str = ""
    access_token: str = ""
    oanda_environment: str = "practice"
    trading_mode: str = "paper"
    instruments: tuple[str, ...] = ("USD_JPY", "EUR_JPY", "EUR_USD")
    granularity: str = "M15"
    candle_count: int = 240
    cycle_seconds: int = 300
    base_units: int = 1000
    max_positions: int = 3
    signal_threshold: float = 0.62
    max_spread_pips: float = 2.0
    stop_loss_pips: float = 25.0
    take_profit_pips: float = 40.0
    max_daily_loss_jpy: float = 3000.0
    max_hold_candles: int = 32
    database_path: Path = Path("data/kfxai.sqlite3")
    judgment_backend: str = "rule_based"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:12b-it-qat"
    brain_url: str = ""
    brain_timeout: int = 330
    enable_control_api: bool = False
    # 戦略: direction(方向モデル) / session(東京レンジ→ロンドンブレイクアウト)
    strategy: str = "direction"
    session_range_start: int = 0      # 東京レンジ開始 UTC時
    session_range_end: int = 7        # 東京レンジ終了 UTC時
    session_entry_until: int = 12     # この UTC時までにブレイクしなければ見送り
    session_close_hour: int = 21      # 強制手仕舞い UTC時
    session_buffer_pips: float = 2.0  # ブレイク確認バッファ
    session_tp_mult: float = 1.5      # TP=レンジ幅×この倍率
    session_max_sl_pips: float = 40.0  # レンジ(=SL幅)がこれ超の日は見送り
    session_min_range_pips: float = 10.0  # レンジ幅下限
    # アリーナの戦略実行エージェント: 各自に枠(max_positions)と仮想予算を割り当て、
    # 予算比の成績で評価する。DD上限超過で新規停止(建玉の決済は継続)。
    agent_budget_jpy: float = 300000.0
    agent_max_drawdown_pct: float = 10.0  # 予算比。既定: 30万×10%=3万円負けたら停止
    agent_daily_loss_jpy: float = 3000.0  # エージェント別の日次損失上限

    @property
    def api_base_url(self) -> str:
        if self.oanda_environment == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"

    def validate(self, require_credentials: bool = False) -> None:
        if self.trading_mode not in {"paper", "practice", "live"}:
            raise ValueError("KFXAI_TRADING_MODE must be paper, practice, or live")
        if self.oanda_environment not in {"practice", "live"}:
            raise ValueError("OANDA_ENVIRONMENT must be practice or live")
        if self.trading_mode == "practice" and self.oanda_environment != "practice":
            raise ValueError("practice trading requires OANDA_ENVIRONMENT=practice")
        if self.trading_mode == "live":
            if self.oanda_environment != "live":
                raise ValueError("live trading requires OANDA_ENVIRONMENT=live")
            if os.getenv("KFXAI_LIVE_ACK", "") != LIVE_ACK:
                raise ValueError(f"live trading requires KFXAI_LIVE_ACK={LIVE_ACK}")
        if require_credentials and (not self.account_id or not self.access_token):
            raise ValueError("OANDA_ACCOUNT_ID and OANDA_ACCESS_TOKEN are required")
        if self.judgment_backend not in {"rule_based", "local_llm", "x402"}:
            raise ValueError("KFXAI_JUDGMENT_BACKEND must be rule_based, local_llm, or x402")
        if self.judgment_backend == "x402" and not self.brain_url:
            raise ValueError("KFXAI_BRAIN_URL is required for the x402 backend")
        if not self.instruments:
            raise ValueError("at least one instrument is required")
        if self.base_units <= 0 or self.max_positions <= 0:
            raise ValueError("base_units and max_positions must be positive")
        if not 0.5 < self.signal_threshold < 1:
            raise ValueError("signal_threshold must be between 0.5 and 1")
        if self.strategy not in {"direction", "session", "arena"}:
            raise ValueError("KFXAI_STRATEGY must be direction, session, or arena")


def load_settings(config_path: str | Path | None = None) -> Settings:
    instruments = tuple(
        item.strip().upper()
        for item in os.getenv("KFXAI_INSTRUMENTS", "USD_JPY,EUR_JPY,EUR_USD,GBP_JPY,AUD_JPY").split(",")
        if item.strip()
    )
    settings = Settings(
        account_id=os.getenv("OANDA_ACCOUNT_ID", "").strip(),
        access_token=os.getenv("OANDA_ACCESS_TOKEN", "").strip(),
        oanda_environment=os.getenv("OANDA_ENVIRONMENT", "practice").strip().lower(),
        trading_mode=os.getenv("KFXAI_TRADING_MODE", "paper").strip().lower(),
        instruments=instruments,
        granularity=os.getenv("KFXAI_GRANULARITY", "M15").strip().upper(),
        candle_count=int(os.getenv("KFXAI_CANDLE_COUNT", "240")),
        cycle_seconds=int(os.getenv("KFXAI_CYCLE_SECONDS", "300")),
        base_units=int(os.getenv("KFXAI_BASE_UNITS", "1000")),
        max_positions=int(os.getenv("KFXAI_MAX_POSITIONS", "3")),
        signal_threshold=float(os.getenv("KFXAI_SIGNAL_THRESHOLD", "0.62")),
        max_spread_pips=float(os.getenv("KFXAI_MAX_SPREAD_PIPS", "2.0")),
        stop_loss_pips=float(os.getenv("KFXAI_STOP_LOSS_PIPS", "25")),
        take_profit_pips=float(os.getenv("KFXAI_TAKE_PROFIT_PIPS", "40")),
        max_daily_loss_jpy=float(os.getenv("KFXAI_MAX_DAILY_LOSS_JPY", "3000")),
        max_hold_candles=int(os.getenv("KFXAI_MAX_HOLD_CANDLES", "32")),
        database_path=Path(os.getenv("KFXAI_DATABASE", "data/kfxai.sqlite3")),
        judgment_backend=os.getenv("KFXAI_JUDGMENT_BACKEND", "rule_based").strip().lower(),
        ollama_url=os.getenv("KFXAI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.getenv("KFXAI_OLLAMA_MODEL", "gemma4:12b-it-qat").strip(),
        brain_url=os.getenv("KFXAI_BRAIN_URL", "").rstrip("/"),
        brain_timeout=int(os.getenv("KFXAI_BRAIN_TIMEOUT", "330")),
        enable_control_api=_bool("KFXAI_ENABLE_CONTROL_API"),
        strategy=os.getenv("KFXAI_STRATEGY", "direction").strip().lower(),
        session_range_start=int(os.getenv("KFXAI_SESSION_RANGE_START", "0")),
        session_range_end=int(os.getenv("KFXAI_SESSION_RANGE_END", "7")),
        session_entry_until=int(os.getenv("KFXAI_SESSION_ENTRY_UNTIL", "12")),
        session_close_hour=int(os.getenv("KFXAI_SESSION_CLOSE_HOUR", "21")),
        session_buffer_pips=float(os.getenv("KFXAI_SESSION_BUFFER_PIPS", "2.0")),
        session_tp_mult=float(os.getenv("KFXAI_SESSION_TP_MULT", "1.5")),
        session_max_sl_pips=float(os.getenv("KFXAI_SESSION_MAX_SL_PIPS", "40")),
        session_min_range_pips=float(os.getenv("KFXAI_SESSION_MIN_RANGE_PIPS", "10")),
        agent_budget_jpy=float(os.getenv("KFXAI_AGENT_BUDGET_JPY", "300000")),
        agent_max_drawdown_pct=float(os.getenv("KFXAI_AGENT_MAX_DRAWDOWN_PCT", "10")),
        agent_daily_loss_jpy=float(os.getenv("KFXAI_AGENT_DAILY_LOSS_JPY", "3000")),
    )
    if config_path:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        allowed = {field.name for field in settings.__dataclass_fields__.values()}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        if "instruments" in payload:
            payload["instruments"] = tuple(payload["instruments"])
        if "database_path" in payload:
            payload["database_path"] = Path(payload["database_path"])
        settings = replace(settings, **payload)
    settings.validate(require_credentials=False)
    return settings

