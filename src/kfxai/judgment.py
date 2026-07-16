from __future__ import annotations

import json
from typing import Any, Protocol

import requests

from . import judgment_rule
from .config import Settings


class JudgmentBackend(Protocol):
    name: str

    def classify_regime(self, ohlcv: dict[str, list[list[Any]]]) -> dict[str, Any]: ...
    def risk_directive(
        self, ohlcv: dict[str, list[list[Any]]], history: list[dict[str, Any]]
    ) -> dict[str, Any]: ...
    def review_trade(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def propose_hypotheses(self, dossier: dict[str, Any]) -> list[dict[str, Any]]: ...


class RuleBackend:
    name = "rule_based"
    classify_regime = staticmethod(judgment_rule.classify_regime)
    risk_directive = staticmethod(judgment_rule.risk_directive)
    review_trade = staticmethod(judgment_rule.review_trade)
    propose_hypotheses = staticmethod(judgment_rule.propose_hypotheses)


class LocalLlmBackend:
    name = "local_llm"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.fallback = RuleBackend()

    def _ask(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You are the judgment brain for a conservative FX paper-trading system. "
            "Use only the supplied facts. Return one JSON object and no markdown.\n"
            f"task={task}\ninput={json.dumps(payload, ensure_ascii=False)}"
        )
        response = requests.post(
            f"{self.settings.ollama_url}/api/generate",
            json={
                "model": self.settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_predict": 500},
            },
            timeout=240,
        )
        response.raise_for_status()
        result = json.loads(response.json()["response"])
        result.setdefault("model", self.settings.ollama_model)
        return result

    def classify_regime(self, ohlcv: dict[str, list[list[Any]]]) -> dict[str, Any]:
        try:
            return self._ask("classify regime as risk_on, risk_off, or neutral", {"ohlcv": ohlcv})
        except Exception:
            return self.fallback.classify_regime(ohlcv)

    def risk_directive(
        self, ohlcv: dict[str, list[list[Any]]], history: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            return self._ask(
                "return directive risk_on, risk_off, or neutral with a short note",
                {"ohlcv": ohlcv, "history": history[-12:]},
            )
        except Exception:
            return self.fallback.risk_directive(ohlcv, history)

    def review_trade(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._ask("postmortem with category, verdict, and lesson", context)
        except Exception:
            return self.fallback.review_trade(context)

    def propose_hypotheses(self, dossier: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            result = self._ask(
                "propose hypotheses; output object with hypotheses list using only signal_threshold",
                dossier,
            )
            return list(result.get("hypotheses") or [])
        except Exception:
            return self.fallback.propose_hypotheses(dossier)


class X402Backend:
    name = "x402"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            self.settings.brain_url + path,
            json=payload,
            timeout=self.settings.brain_timeout,
        )
        if response.status_code == 402:
            raise RuntimeError(
                "x402 payment required; call KFXAI_BRAIN_URL through an x402-aware wallet proxy"
            )
        response.raise_for_status()
        return response.json()

    def classify_regime(self, ohlcv: dict[str, list[list[Any]]]) -> dict[str, Any]:
        return self._post("/v1/judge/regime", {"ohlcv_by_instrument": ohlcv})

    def risk_directive(
        self, ohlcv: dict[str, list[list[Any]]], history: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._post(
            "/v1/judge/directive", {"ohlcv_by_instrument": ohlcv, "history": history[-12:]}
        )

    def review_trade(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._post("/v1/judge/postmortem", {"ctx": context})

    def propose_hypotheses(self, dossier: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._post("/v1/research/hypotheses", {"dossier": dossier}).get("hypotheses") or [])


def build_backend(settings: Settings) -> JudgmentBackend:
    if settings.judgment_backend == "local_llm":
        return LocalLlmBackend(settings)
    if settings.judgment_backend == "x402":
        return X402Backend(settings)
    return RuleBackend()

