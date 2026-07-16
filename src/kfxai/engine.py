from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .database import Database
from .judgment import JudgmentBackend, build_backend
from .models import Candle, Price, Signal, utc_now_iso
from .oanda import OandaClient
from .predictor import DirectionModel
from .strategies import build_strategies
from .strategy_session import session_should_close, session_signal


def market_is_open(now: datetime | None = None) -> bool:
    """Conservative FX market gate using the common New York weekend boundary."""
    now = now or datetime.now(timezone.utc)
    weekday = now.weekday()
    if weekday == 5:
        return False
    if weekday == 6 and now.hour < 21:
        return False
    if weekday == 4 and now.hour >= 21:
        return False
    return True


def price_targets(price: Price, side: str, stop_pips: float, take_pips: float) -> tuple[float, float]:
    entry = price.ask if side == "long" else price.bid
    if side == "long":
        return entry - stop_pips * price.pip_size, entry + take_pips * price.pip_size
    return entry + stop_pips * price.pip_size, entry - take_pips * price.pip_size


def estimate_pnl_jpy(
    instrument: str, side: str, units: int, open_price: float, close_price: float,
    prices: dict[str, Price],
) -> float:
    direction = 1.0 if side == "long" else -1.0
    quote_pnl = (close_price - open_price) * units * direction
    if instrument.endswith("_JPY"):
        return quote_pnl
    quote = instrument.split("_", 1)[1]
    if quote == "USD" and "USD_JPY" in prices:
        return quote_pnl * prices["USD_JPY"].mid
    return quote_pnl


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        client: OandaClient | None = None,
        database: Database | None = None,
        judgment: JudgmentBackend | None = None,
    ):
        self.settings = settings
        self.client = client or OandaClient(settings)
        self.db = database or Database(settings.database_path)
        self.judgment = judgment or build_backend(settings)
        # 戦略アリーナ(KFXAI_STRATEGY=arena): 複数戦略を並走させ戦略別台帳で競わせる
        self.arena = build_strategies(settings) if settings.strategy == "arena" else []
        self._arena_by_name = {s.name: s for s in self.arena}
        if self.arena:
            print(f"[arena] strategies: {[s.name for s in self.arena]}")

    def _fetch_market(self) -> tuple[dict[str, list[Candle]], dict[str, Price], list[str]]:
        candle_map: dict[str, list[Candle]] = {}
        errors: list[str] = []
        for instrument in self.settings.instruments:
            try:
                candles = [
                    candle for candle in self.client.candles(
                        instrument, self.settings.granularity, self.settings.candle_count
                    ) if candle.complete
                ]
                if len(candles) < 40:
                    raise ValueError(f"only {len(candles)} completed candles")
                candle_map[instrument] = candles
                self.db.save_candles(instrument, self.settings.granularity, candles)
            except Exception as exc:
                errors.append(f"{instrument}: candles: {exc}")
        try:
            # 監視リスト外でも建玉が残っている銘柄(リスト変更後の孤児)の価格を含めて取る。
            # これが無いと孤児ポジションの決済処理が永遠にスキップされる。
            open_instruments = {t["instrument"] for t in self.db.open_paper_trades()}
            wanted = tuple(dict.fromkeys(list(candle_map) + sorted(open_instruments)))
            prices = self.client.prices(wanted) if wanted else {}
        except Exception as exc:
            prices = {}
            errors.append(f"pricing: {exc}")
        return candle_map, prices, errors

    @staticmethod
    def _ohlcv(candle_map: dict[str, list[Candle]]) -> dict[str, list[list[Any]]]:
        return {
            instrument: [candle.as_ohlcv() for candle in candles[-60:]]
            for instrument, candles in candle_map.items()
        }

    def _signals(
        self,
        candle_map: dict[str, list[Candle]],
        regime: dict[str, Any],
        directive: dict[str, Any],
    ) -> dict[str, Signal]:
        if self.settings.strategy == "session":
            return self._session_signals(candle_map, directive)
        result: dict[str, Signal] = {}
        for instrument, candles in candle_map.items():
            pip_size = 0.01 if instrument.endswith("_JPY") else 0.0001
            model = DirectionModel()
            model.fit(
                candles,
                pip_size=pip_size,
                stop_pips=self.settings.stop_loss_pips,
                take_pips=self.settings.take_profit_pips,
                max_hold=self.settings.max_hold_candles,
            )
            probability, features = model.predict(candles)
            confidence = abs(probability - 0.5) * 2
            if probability >= self.settings.signal_threshold:
                action = "buy"
            elif probability <= 1.0 - self.settings.signal_threshold:
                action = "sell"
            else:
                action = "hold"
            if directive.get("directive") == "risk_off" and action != "hold":
                reason = "risk directive blocks new exposure"
                action = "hold"
            else:
                reason = (
                    f"p_up={probability:.3f}, threshold={self.settings.signal_threshold:.3f}; "
                    f"{directive.get('note', '')}"
                )
            result[instrument] = Signal(
                instrument=instrument,
                action=action,
                probability_up=round(probability, 6),
                confidence=round(confidence, 6),
                regime=str(regime.get("regime", "neutral")),
                directive=str(directive.get("directive", "neutral")),
                reason=reason,
                model=f"direction-logistic/{model.samples}+{directive.get('model', 'unknown')}",
                features={key: round(value, 8) for key, value in features.items()},
            )
        return result

    def _session_signals(
        self,
        candle_map: dict[str, list[Candle]],
        directive: dict[str, Any],
    ) -> dict[str, Signal]:
        """セッションブレイクアウト戦略の信号。1銘柄1日1取引・risk_offで遮断。"""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        result: dict[str, Signal] = {}
        for instrument, candles in candle_map.items():
            traded = self.db.get_state(f"session_traded:{instrument}") == today
            signal = session_signal(instrument, candles, self.settings, now, traded)
            if signal.action != "hold" and directive.get("directive") == "risk_off":
                signal = Signal(
                    instrument=instrument, action="hold", probability_up=0.5, confidence=0.0,
                    regime="session", directive="risk_off",
                    reason="risk directive blocks new exposure",
                    model=signal.model, features=signal.features,
                )
            result[instrument] = signal
        return result

    def _review_closed_trade(self, trade: dict[str, Any]) -> None:
        review = self.judgment.review_trade(trade)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO journal(trade_id,created_at,category,verdict,lesson,model) "
                "VALUES(?,?,?,?,?,?)",
                (
                    trade["id"], utc_now_iso(), review.get("category", "other"),
                    review.get("verdict", "review"), review.get("lesson", ""),
                    review.get("model", self.judgment.name),
                ),
            )
            conn.execute("UPDATE paper_trades SET reviewed=1 WHERE id=?", (trade["id"],))

    def _manage_paper_trades(self, prices: dict[str, Price]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for trade in self.db.open_paper_trades():
            price = prices.get(trade["instrument"])
            if not price:
                continue
            current = price.bid if trade["side"] == "long" else price.ask
            reason = ""
            if trade["side"] == "long":
                if current <= trade["stop_price"]:
                    reason = "stop_loss"
                elif current >= trade["take_price"]:
                    reason = "take_profit"
            else:
                if current >= trade["stop_price"]:
                    reason = "stop_loss"
                elif current <= trade["take_price"]:
                    reason = "take_profit"
            if not reason and trade["instrument"] not in self.settings.instruments:
                # 監視リストから外れた銘柄の孤児ポジション(旧戦略の遺物)は即時手仕舞い
                reason = "orphaned"
            if not reason:
                strategy = self._arena_by_name.get(trade.get("strategy") or "")
                if strategy is not None:
                    # アリーナ: 各戦略の手仕舞いルール(時刻 or 保有時間上限)
                    if strategy.close_on_session_end and session_should_close(
                            datetime.now(timezone.utc), self.settings):
                        reason = "session_close"
                    elif strategy.max_hold_minutes is not None:
                        held_min = (trade["bars_held"] + 1) * self.settings.cycle_seconds / 60
                        if held_min >= strategy.max_hold_minutes:
                            reason = "max_hold"
                elif self.settings.strategy in ("session", "arena"):
                    # セッション戦略(単独モード) or アリーナのロースター外の遺物
                    if session_should_close(datetime.now(timezone.utc), self.settings):
                        reason = "session_close"
                elif trade["bars_held"] + 1 >= self.settings.max_hold_candles:
                    reason = "max_hold"
            self.db.advance_paper_trade(trade["id"])
            if reason:
                pnl = estimate_pnl_jpy(
                    trade["instrument"], trade["side"], trade["units"],
                    trade["open_price"], current, prices,
                )
                self.db.close_paper_trade(trade["id"], current, pnl, reason)
                closed = {**trade, "close_price": current, "pnl_jpy": pnl, "exit_reason": reason}
                self._review_closed_trade(closed)
                events.append({"trade_id": trade["id"], "event": "closed", "reason": reason, "pnl_jpy": pnl})
        return events

    def _risk_allows(
        self, signal: Signal, price: Price | None, position_taken: bool, open_count: int,
    ) -> tuple[bool, str]:
        """position_taken/open_countは単独モードでは銘柄集合と全体数、
        アリーナでは(戦略,銘柄)キーの有無と戦略ごとの建玉数を渡す。"""
        if signal.action == "hold":
            return False, "no entry signal"
        if not market_is_open():
            return False, "FX market is closed"
        if price is None or price.status != "tradeable":
            return False, "instrument is not tradeable"
        if price.spread_pips > self.settings.max_spread_pips:
            return False, f"spread {price.spread_pips:.2f} pips exceeds limit"
        if position_taken:
            return False, "position already exists"
        if open_count >= self.settings.max_positions:
            return False, "max positions reached"
        if self.db.today_pnl_jpy() <= -self.settings.max_daily_loss_jpy:
            return False, "daily loss limit reached"
        return True, "allowed"

    def run_cycle(self) -> dict[str, Any]:
        cycle_id = self.db.start_cycle()
        started = utc_now_iso()
        try:
            candle_map, prices, errors = self._fetch_market()
            if not candle_map:
                raise RuntimeError("no instrument has enough OANDA candles")
            ohlcv = self._ohlcv(candle_map)
            history = self.db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT 12")
            regime = self.judgment.classify_regime(ohlcv)
            directive = self.judgment.risk_directive(ohlcv, history)
            self.db.set_state("regime", regime)
            self.db.set_state("directive", directive)
            self.db.set_state("backend", self.judgment.name)

            paper_events = self._manage_paper_trades(prices) if self.settings.trading_mode == "paper" else []
            if self.settings.trading_mode == "paper":
                open_instruments = {trade["instrument"] for trade in self.db.open_paper_trades()}
            else:
                positions = self.client.open_positions()
                open_instruments = {
                    position["instrument"] for position in positions
                    if float(position.get("long", {}).get("units", 0))
                    or float(position.get("short", {}).get("units", 0))
                }
            open_count = len(open_instruments)
            actions: list[dict[str, Any]] = []
            # エントリー候補を作る。単独モード=銘柄ごと1信号、アリーナ=戦略×銘柄。
            now_utc = datetime.now(timezone.utc)
            today = now_utc.strftime("%Y-%m-%d")
            if self.arena:
                open_rows = self.db.open_paper_trades() if self.settings.trading_mode == "paper" else []
                open_keys = {(t.get("strategy") or "session", t["instrument"]) for t in open_rows}
                strat_counts: dict[str, int] = {}
                for key, _inst in open_keys:
                    strat_counts[key] = strat_counts.get(key, 0) + 1
                entries: list[tuple[Any, Signal]] = []
                for strat in self.arena:
                    for instrument in candle_map:
                        already = (strat.name, instrument) in open_keys
                        daily_key = (f"session_traded:{instrument}" if strat.name == "session"
                                     else f"traded:{strat.name}:{instrument}")
                        if strat.daily_limit and self.db.get_state(daily_key) == today:
                            signal = Signal(
                                instrument=instrument, action="hold", probability_up=0.5,
                                confidence=0.0, regime="arena",
                                directive=str(directive.get("directive", "neutral")),
                                reason="already traded today", model=strat.name, features={})
                        else:
                            signal = strat.signal(instrument, candle_map[instrument],
                                                  self.settings, now_utc, already)
                        if signal.action != "hold" and directive.get("directive") == "risk_off":
                            signal = Signal(**{**signal.__dict__, "action": "hold",
                                               "reason": "risk directive blocks new exposure"})
                        entries.append((strat, signal))
            else:
                entries = [(None, s) for s in self._signals(candle_map, regime, directive).values()]

            for strat, signal in entries:
                instrument = signal.instrument
                price = prices.get(instrument)
                decision_id = self.db.record_decision(signal, price.spread_pips if price else None)
                if strat is not None:
                    position_taken = (strat.name, instrument) in open_keys
                    slot_count = strat_counts.get(strat.name, 0)
                else:
                    position_taken = instrument in open_instruments
                    slot_count = open_count
                allowed, gate_reason = self._risk_allows(signal, price, position_taken, slot_count)
                action = {"decision_id": decision_id, **signal.to_dict(), "gate": gate_reason}
                if allowed and price:
                    side = "long" if signal.action == "buy" else "short"
                    signed_units = self.settings.base_units if side == "long" else -self.settings.base_units
                    if signal.stop_price is not None and signal.take_price is not None:
                        # 戦略が価格ベースのSL/TPを指定(セッションブレイクアウト等)
                        stop, take = signal.stop_price, signal.take_price
                    else:
                        stop, take = price_targets(
                            price, side, self.settings.stop_loss_pips, self.settings.take_profit_pips
                        )
                    strategy_name = strat.name if strat is not None else self.settings.strategy
                    if self.settings.trading_mode == "paper":
                        entry = price.ask if side == "long" else price.bid
                        reference = str(self.db.open_paper_trade(
                            instrument, side, abs(signed_units), entry, stop, take,
                            strategy=strategy_name,
                        ))
                    else:
                        response = self.client.market_order(instrument, signed_units, stop, take)
                        transaction = response.get("orderFillTransaction") or response.get("orderCreateTransaction") or {}
                        reference = str(transaction.get("id", "unknown"))
                    self.db.mark_executed(decision_id, self.settings.trading_mode, reference)
                    daily_limited = (strat.daily_limit if strat is not None
                                     else self.settings.strategy == "session")
                    if daily_limited:
                        daily_key = (f"session_traded:{instrument}" if strategy_name == "session"
                                     else f"traded:{strategy_name}:{instrument}")
                        self.db.set_state(daily_key, today)
                    action.update({"executed": True, "reference": reference, "stop": stop, "take": take})
                    if strat is not None:
                        open_keys.add((strat.name, instrument))
                        strat_counts[strat.name] = strat_counts.get(strat.name, 0) + 1
                    else:
                        open_instruments.add(instrument)
                        open_count += 1
                else:
                    action["executed"] = False
                actions.append(action)

            summary = {
                "cycle_id": cycle_id,
                "started_at": started,
                "finished_at": utc_now_iso(),
                "mode": self.settings.trading_mode,
                "backend": self.judgment.name,
                "regime": regime,
                "directive": directive,
                "actions": actions,
                "paper_events": paper_events,
                "errors": errors,
            }
            self.db.set_state("last_cycle", summary)
            self.db.finish_cycle(cycle_id, "done", json.dumps({"errors": errors}, ensure_ascii=False))
            return summary
        except Exception as exc:
            self.db.finish_cycle(cycle_id, "failed", str(exc))
            self.db.set_state("last_error", {"at": utc_now_iso(), "error": str(exc)})
            raise

